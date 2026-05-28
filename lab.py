#!/usr/bin/env python3
"""Research Lab boot script v0.1.

Loads doctrine and prompts, resolves per-role models, instantiates agents,
registers MCP capabilities, and routes tasks to the Director entrypoint.

Local CLI (no Slack required):
    python lab.py "Initiate coverage on 9988 HK"

Slack bridge:
    python lab.py --slack
    python lab.py --test-slack
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import traceback
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from skills.peer_regression import run_peer_regression

ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

LAB_MD = ROOT / "lab.md"
COVERAGE_MD = ROOT / "coverage.md"
COVERAGE_STATE = ROOT / "coverage_state"

DOTTED_TICKER = re.compile(
    r"\b([A-Z0-9]{1,6}\.(HK|US|SHG|SHE|LSE))\b",
    re.IGNORECASE,
)
BARE_TICKER = re.compile(
    r"\b([A-Z0-9]{1,6})\s+(HK|US|SHG|SHE)\b",
    re.IGNORECASE,
)

COVERAGE_STATE_FILES: dict[str, str] = {
    "standing_thesis": "standing_thesis.md",
    "kpis": "kpis.md",
    "regime_history": "regime_history.md",
    "locked_sections": "locked_sections.md",
}
PROMPT_PATHS: dict[str, Path] = {
    "director": ROOT / "prompts" / "director.md",
    "us_analyst": ROOT / "prompts" / "regional" / "us.md",
    "hk_analyst": ROOT / "prompts" / "regional" / "hk.md",
    "china_ah_analyst": ROOT / "prompts" / "regional" / "china_ah.md",
    "macro_specialist": ROOT / "prompts" / "specialists" / "macro.md",
    "sector_specialist": ROOT / "prompts" / "specialists" / "sector.md",
    "valuation_specialist": ROOT / "prompts" / "specialists" / "valuation.md",
    "risk_specialist": ROOT / "prompts" / "specialists" / "risk.md",
    "coverage_agent": ROOT / "prompts" / "coverage" / "agent.md",
}

ROLE_MODEL_ENV: dict[str, tuple[str, ...]] = {
    "director": ("LAB_MODEL_DIRECTOR",),
    "coverage_agent": ("LAB_MODEL_COVERAGE",),
    "us_analyst": ("LAB_MODEL_US_ANALYST", "LAB_MODEL_ANALYST"),
    "hk_analyst": ("LAB_MODEL_HK_ANALYST", "LAB_MODEL_ANALYST"),
    "china_ah_analyst": ("LAB_MODEL_CN_ANALYST", "LAB_MODEL_ANALYST"),
    "macro_specialist": ("LAB_MODEL_SPECIALIST",),
    "sector_specialist": ("LAB_MODEL_SPECIALIST",),
    "valuation_specialist": ("LAB_MODEL_SPECIALIST",),
    "risk_specialist": ("LAB_MODEL_SPECIALIST",),
}

EODHD_SERVER = ROOT / "mcp_servers" / "eodhd" / "server.py"

# Self-describing capability metadata per SPEC §5.1 (boot wiring only; no global registry).
# TODO: add premium institutional data, filing repositories, news MCPs when implemented.
CAPABILITIES: dict[str, dict[str, Any]] = {
    "standard_market_data": {
        "name": "eodhd",
        "version": "0.1.0",
        "quality_tier": 2,
        "domain": "standard market data",
        "server_path": str(EODHD_SERVER),
        "tools": [
            "get_price_history",
            "get_fundamentals",
            "get_earnings_history",
            "search_ticker",
        ],
        "assigned_roles": [
            "us_analyst",
            "hk_analyst",
            "china_ah_analyst",
            "valuation_specialist",
            "sector_specialist",
        ],
    },
}

SPECIALIST_ORDER = ["sector_specialist", "valuation_specialist", "risk_specialist"]
REGIONAL_ROLES: dict[str, str | list[str]] = {
    "HK": "hk_analyst",
    "US": "us_analyst",
    "CN": "china_ah_analyst",
    "DUAL": ["hk_analyst", "china_ah_analyst"],
}

DEFAULT_PEERS: dict[str, list[str]] = {
    "9988.HK": [
        "700.HK", "3690.HK", "9618.HK", "BABA.US",
        "JD.US", "PDD.US", "BIDU.US", "9999.HK",
    ],
    "700.HK": [
        "9988.HK", "3690.HK", "9618.HK", "NTES.US",
        "9999.HK", "1024.HK", "2382.HK",
    ],
    "CRM.US": [
        "NOW.US", "ORCL.US", "SAP.US", "ADBE.US",
        "WDAY.US", "MDB.US", "HUBS.US",
    ],
}

MEMO_SECTION_TITLES: list[tuple[str, str]] = [
    ("1", "Investment Thesis"),
    ("2", "Factor Regime"),
    ("3", "Fundamental Snapshot"),
    ("4", "Regional Context"),
    ("5", "Sell-Side Consensus"),
    ("6", "Scenario Analysis"),
    ("7", "Catalysts & Timeline"),
    ("8", "Risks"),
]

SLACK_SECTION_LIMIT = 3900

SKILLS: dict[str, dict[str, Any]] = {
    "peer_regression": {
        "module": "skills.peer_regression",
        "entrypoint": run_peer_regression,
        "assigned_roles": [
            "us_analyst",
            "hk_analyst",
            "china_ah_analyst",
        ],
    },
        }


@dataclass
class SlackBridgeState:
    """In-memory Slack session state (not persisted across restarts)."""

    section_num_by_ts: dict[str, str] = field(default_factory=dict)
    section_content_by_ts: dict[str, str] = field(default_factory=dict)
    memo_parent_by_section_ts: dict[str, str] = field(default_factory=dict)
    memo_meta_by_ts: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_task: str | None = None
    last_ticker: str | None = None
    compliance_holds: set[str] = field(default_factory=set)


@dataclass
class DispatchPlan:
    ticker: str | None
    exchange: str | None
    region: str
    rigor: str
    is_covered: bool
    agents_needed: list[str]
    task_type: str
    raw_classification: str


class LabConfigError(RuntimeError):
    """Missing or invalid lab configuration."""


@dataclass
class LabConfig:
    """Runtime configuration loaded from environment."""

    model_director: str
    model_analyst: str
    model_specialist: str
    model_coverage: str
    model_us_analyst: str | None = None
    model_hk_analyst: str | None = None
    model_cn_analyst: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    deepseek_api_key: str | None = None
    dashscope_api_key: str | None = None
    dashscope_api_base: str | None = None
    openrouter_api_key: str | None = None
    eodhd_api_key: str | None = None
    slack_bot_token: str | None = None
    slack_app_token: str | None = None
    slack_channel_id: str | None = None
    resolved_models: dict[str, str] = field(default_factory=dict)


@dataclass
class BootReport:
    """Structured boot summary (no secrets)."""

    lab_md_loaded: bool
    coverage_md_hash: str
    prompt_roles: list[str]
    models_by_role: dict[str, str]
    capabilities: list[str]
    provider_mode: str
    director_ready: bool
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "lab_md_loaded": self.lab_md_loaded,
            "coverage_md_hash": self.coverage_md_hash,
            "prompt_roles": self.prompt_roles,
            "models_by_role": self.models_by_role,
            "capabilities": self.capabilities,
            "provider_mode": self.provider_mode,
            "director_ready": self.director_ready,
            "message": self.message,
        }


def load_text(path: Path) -> str:
    """Read a UTF-8 text file; fail fast if missing."""
    if not path.is_file():
        raise LabConfigError(f"Required file not found: {path}")
    return path.read_text(encoding="utf-8")


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise LabConfigError(f"Missing required environment variable: {name}")
    return value


def load_config() -> LabConfig:
    """Load .env and validate required model configuration."""
    load_dotenv(ROOT / ".env")
    load_dotenv()

    config = LabConfig(
        model_director=_require_env("LAB_MODEL_DIRECTOR"),
        model_analyst=_require_env("LAB_MODEL_ANALYST"),
        model_specialist=_require_env("LAB_MODEL_SPECIALIST"),
        model_coverage=_require_env("LAB_MODEL_COVERAGE"),
        model_us_analyst=os.getenv("LAB_MODEL_US_ANALYST", "").strip() or None,
        model_hk_analyst=os.getenv("LAB_MODEL_HK_ANALYST", "").strip() or None,
        model_cn_analyst=os.getenv("LAB_MODEL_CN_ANALYST", "").strip() or None,
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip() or None,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip() or None,
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", "").strip() or None,
        dashscope_api_key=os.getenv("DASHSCOPE_API_KEY", "").strip() or None,
        dashscope_api_base=os.getenv("DASHSCOPE_API_BASE", "").strip() or None,
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", "").strip() or None,
        eodhd_api_key=os.getenv("EODHD_API_KEY", "").strip() or None,
        slack_bot_token=os.getenv("SLACK_BOT_TOKEN", "").strip() or None,
        slack_app_token=os.getenv("SLACK_APP_TOKEN", "").strip() or None,
        slack_channel_id=os.getenv("SLACK_CHANNEL_ID", "").strip() or None,
    )

    for role in PROMPT_PATHS:
        config.resolved_models[role] = resolve_model_for_role(role, config)

    unique_models = set(config.resolved_models.values())
    for model in unique_models:
        validate_provider_key_for_model(model, config)

    return config


def resolve_model_for_role(role_name: str, config: LabConfig) -> str:
    """Resolve the model string for a role per .env.example override rules."""
    env_names = ROLE_MODEL_ENV.get(role_name)
    if not env_names:
        raise LabConfigError(f"Unknown role for model resolution: {role_name}")

    for env_name in env_names:
        override = getattr(config, _config_attr_for_env(env_name), None)
        if override:
            return override

    fallback_env = env_names[-1]
    return getattr(config, _config_attr_for_env(fallback_env))


def _config_attr_for_env(env_name: str) -> str:
    mapping = {
        "LAB_MODEL_DIRECTOR": "model_director",
        "LAB_MODEL_ANALYST": "model_analyst",
        "LAB_MODEL_SPECIALIST": "model_specialist",
        "LAB_MODEL_COVERAGE": "model_coverage",
        "LAB_MODEL_US_ANALYST": "model_us_analyst",
        "LAB_MODEL_HK_ANALYST": "model_hk_analyst",
        "LAB_MODEL_CN_ANALYST": "model_cn_analyst",
    }
    return mapping[env_name]


def validate_provider_key_for_model(model: str, config: LabConfig) -> None:
    """Fail fast when the selected model's provider API key is absent."""
    model_lower = model.lower()

    if model_lower.startswith("openrouter/"):
        if not config.openrouter_api_key:
            raise LabConfigError(
                "Model "
                f"{model!r} requires OPENROUTER_API_KEY (set in .env)."
            )
        return

    if model_lower.startswith("deepseek/"):
        if not config.deepseek_api_key:
            raise LabConfigError(
                "Model "
                f"{model!r} requires DEEPSEEK_API_KEY (set in .env)."
            )
        return

    if model_lower.startswith("dashscope/"):
        if not config.dashscope_api_key:
            raise LabConfigError(
                "Model "
                f"{model!r} requires DASHSCOPE_API_KEY (set in .env)."
            )
        return

    if "claude" in model_lower or model_lower.startswith("anthropic/"):
        if not config.anthropic_api_key:
            raise LabConfigError(
                "Model "
                f"{model!r} requires ANTHROPIC_API_KEY (set in .env)."
            )
        return

    if model_lower.startswith("openai/") or model_lower.startswith("gpt-"):
        if not config.openai_api_key:
            raise LabConfigError(
                "Model "
                f"{model!r} requires OPENAI_API_KEY (set in .env)."
            )
        return

    # LiteLLM-backed providers (deepseek/, dashscope/, claude-*) handled above.
    # Unprefixed OpenAI-compatible names fall through to OPENAI_API_KEY.
    if not config.openai_api_key:
        raise LabConfigError(
            "Model "
            f"{model!r} has no recognized provider prefix; "
            "set OPENAI_API_KEY or use a prefixed model "
            "(openrouter/, deepseek/, dashscope/, claude-*)."
        )


def configure_provider_environment(config: LabConfig) -> str:
    """Wire provider API keys and base URLs once (LiteLLM reads env)."""
    if config.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", config.openai_api_key)
    if config.anthropic_api_key:
        os.environ.setdefault("ANTHROPIC_API_KEY", config.anthropic_api_key)
    if config.deepseek_api_key:
        os.environ.setdefault("DEEPSEEK_API_KEY", config.deepseek_api_key)
    if config.dashscope_api_key:
        os.environ.setdefault("DASHSCOPE_API_KEY", config.dashscope_api_key)
    if config.dashscope_api_base:
        os.environ.setdefault("DASHSCOPE_API_BASE", config.dashscope_api_base)
    if config.openrouter_api_key:
        os.environ.setdefault("OPENROUTER_API_KEY", config.openrouter_api_key)

    # SPEC §6: OpenAI Agents SDK + LiteLLM adapter for multi-provider models.
    os.environ.setdefault("LITELLM_LOG", "ERROR")
    return "openai-agents + LitellmProvider"


def load_prompt_registry() -> dict[str, str]:
    """Load all required prompts and doctrine files into memory."""
    registry: dict[str, str] = {}
    for role, path in PROMPT_PATHS.items():
        registry[role] = load_text(path)
    registry["lab_md"] = load_text(LAB_MD)
    registry["coverage_md"] = load_text(COVERAGE_MD)
    return registry


def coverage_md_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def detect_ticker(task: str) -> str | None:
    """Detect TICKER.EXCHANGE from task text; return None if not found."""
    match = DOTTED_TICKER.search(task)
    if match:
        return match.group(1).upper()
    match = BARE_TICKER.search(task)
    if match:
        return f"{match.group(1).upper()}.{match.group(2).upper()}"
    return None


def _parse_coverage_active_table(coverage_md_text: str) -> list[str]:
    """Parse ticker column from the ## Active table in coverage.md."""
    tickers: list[str] = []
    in_active = False
    for line in coverage_md_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Active"):
            in_active = True
            continue
        if in_active and stripped.startswith("## "):
            break
        if not in_active or not stripped.startswith("|"):
            continue
        if "---" in stripped or stripped.lower().startswith("| ticker"):
            continue
        parts = [p.strip() for p in stripped.split("|") if p.strip()]
        if parts:
            tickers.append(parts[0].upper())
    return tickers


def get_covered_tickers(coverage_md_text: str) -> list[str]:
    """Return all tickers in the Active table of coverage.md."""
    try:
        return _parse_coverage_active_table(coverage_md_text)
    except Exception as exc:
        _log_step("coverage_parse", status="warning", error=str(exc))
        return []


def is_ticker_covered(ticker: str, coverage_md_text: str) -> bool:
    """
    Return True if ticker appears in the Active table of coverage.md.
    Matches the ticker column exactly (case-insensitive). Never raises.
    """
    if not ticker or not ticker.strip():
        return False
    try:
        normalized = ticker.strip().upper()
        return normalized in {t.upper() for t in get_covered_tickers(coverage_md_text)}
    except Exception as exc:
        _log_step("coverage_parse", status="warning", error=str(exc))
        return False


def load_coverage_state(ticker: str) -> dict[str, str]:
    """
    Load coverage_state/[TICKER]/ files into a dict.
    Returns only files that exist; silently skips missing files.
    Never raises — Coverage Agent handles gaps at runtime.
    """
    state_dir = COVERAGE_STATE / ticker
    if not state_dir.is_dir():
        return {}

    loaded: dict[str, str] = {}
    for key, filename in COVERAGE_STATE_FILES.items():
        path = state_dir / filename
        if not path.is_file():
            continue
        try:
            loaded[key] = path.read_text(encoding="utf-8")
        except OSError:
            continue
    return loaded


def build_coverage_context(ticker: str, state: dict[str, str]) -> str:
    """
    Format loaded coverage_state files into a context block for
    the Director. Returns empty string if state is empty.
    """
    if not state:
        return ""

    sections: list[str] = []
    for key, filename in COVERAGE_STATE_FILES.items():
        content = state.get(key)
        if content:
            sections.append(f"## {filename}\n\n{content}")

    body = "\n\n".join(sections)
    return (
        f"---\n\n# Coverage state — {ticker} "
        f"(coverage_state/{ticker}/)\n\n{body}"
    )


def build_director_instructions(
    prompts: dict[str, str],
    coverage_context: str = "",
) -> str:
    """Director receives doctrine + active context per SPEC §2 bootstrap."""
    instructions = (
        prompts["director"]
        + "\n\n---\n\n# Active doctrine (lab.md)\n\n"
        + prompts["lab_md"]
        + "\n\n---\n\n# Active context (coverage.md)\n\n"
        + prompts["coverage_md"]
    )
    if coverage_context:
        instructions += "\n\n" + coverage_context
    return instructions


def build_peer_regression_tool() -> Any:
    """Wrap peer_regression skill as an Agents SDK function tool."""
    from agents import function_tool

    @function_tool
    def peer_regression_tool(
        target_ticker: str,
        peer_tickers: list[str],
        lookback_months: int = 12,
    ) -> str:
        """
        Run OLS peer regression for Phase 1 factor discovery.
        Given a target ticker and list of peer tickers, fetches trailing price
        history and fundamentals, runs regression, and returns top explanatory
        factors with direction and t-statistics. Use at the start of Phase 1
        before deriving the FactorRegime. Returns JSON string of PeerRegressionResult.
        """
        result = run_peer_regression(
            target_ticker=target_ticker,
            peer_tickers=peer_tickers,
            lookback_months=lookback_months,
        )
        return json.dumps(asdict(result))

    return peer_regression_tool


def build_agents(
    prompts: dict[str, str],
    config: LabConfig,
    mcp_server: Any | None,
) -> dict[str, Any]:
    """Instantiate agents for programmatic pipeline — no SDK handoffs."""
    from agents import Agent

    mcp_roles = set(CAPABILITIES["standard_market_data"]["assigned_roles"])
    mcp_list = [mcp_server] if mcp_server is not None else []
    peer_tool = build_peer_regression_tool()
    regional_tools = [peer_tool]

    agents: dict[str, Any] = {}
    for role in PROMPT_PATHS:
        tools: list[Any] = []
        if role in ("us_analyst", "hk_analyst", "china_ah_analyst"):
            tools = regional_tools
        agents[role] = Agent(
            name=role,
            instructions=prompts[role],
            model=config.resolved_models[role],
            mcp_servers=mcp_list if role in mcp_roles else [],
            tools=tools,
        )
    return agents


def build_step_agent(
    role: str,
    instructions: str,
    config: LabConfig,
    mcp_server: Any | None,
    tools: list[Any] | None = None,
) -> Any:
    """Create a single-step agent with overridden instructions."""
    from agents import Agent

    mcp_roles = set(CAPABILITIES["standard_market_data"]["assigned_roles"])
    mcp_list = [mcp_server] if mcp_server is not None else []
    if tools is None and role in ("us_analyst", "hk_analyst", "china_ah_analyst"):
        tools = [build_peer_regression_tool()]

    return Agent(
        name=role,
        instructions=instructions,
        model=config.resolved_models[role],
        mcp_servers=mcp_list if role in mcp_roles else [],
        tools=tools or [],
    )


def build_eodhd_stdio_server(config: LabConfig) -> Any:
    """Create MCPServerStdio params for the EODHD subprocess."""
    from agents.mcp import MCPServerStdio

    env = os.environ.copy()
    if config.eodhd_api_key:
        env["EODHD_API_KEY"] = config.eodhd_api_key

    if not EODHD_SERVER.is_file():
        raise LabConfigError(f"EODHD MCP server not found: {EODHD_SERVER}")

    return MCPServerStdio(
        name=CAPABILITIES["standard_market_data"]["name"],
        params={
            "command": sys.executable,
            "args": [str(EODHD_SERVER)],
            "env": env,
            "cwd": str(EODHD_SERVER.parent),
        },
        cache_tools_list=True,
    )


def boot(config: LabConfig, prompts: dict[str, str]) -> BootReport:
    """Validate boot path without invoking the Director."""
    return BootReport(
        lab_md_loaded=bool(prompts.get("lab_md")),
        coverage_md_hash=coverage_md_hash(prompts["coverage_md"]),
        prompt_roles=sorted(PROMPT_PATHS.keys()),
        models_by_role=dict(config.resolved_models),
        capabilities=list(CAPABILITIES.keys()),
        provider_mode=configure_provider_environment(config),
        director_ready=True,
        message="Boot complete; Director entrypoint ready.",
    )


def resolve_max_output_tokens() -> int:
    """Cap LLM output tokens — OpenRouter pre-checks cost against max_tokens."""
    raw = os.getenv("LAB_MAX_OUTPUT_TOKENS", "4096").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise LabConfigError(
            f"LAB_MAX_OUTPUT_TOKENS must be an integer, got {raw!r}."
        ) from exc
    if value < 256:
        raise LabConfigError("LAB_MAX_OUTPUT_TOKENS must be at least 256.")
    return value


def _log_step(step: str, **kwargs: Any) -> None:
    print(json.dumps({"step": step, **kwargs}), flush=True)


def extract_md_section(text: str, heading_prefix: str) -> str:
    """Extract a markdown section starting at a ## heading."""
    marker = f"## {heading_prefix}"
    start = text.find(marker)
    if start == -1:
        return ""
    rest = text[start + len(marker) :]
    next_heading = rest.find("\n## ")
    if next_heading == -1:
        return marker + rest
    return marker + rest[:next_heading]


def _split_ticker_exchange(ticker: str | None) -> tuple[str | None, str | None]:
    if not ticker:
        return None, None
    if "." in ticker:
        code, exchange = ticker.rsplit(".", 1)
        return code, exchange
    return ticker, None


def ticker_in_coverage_md(ticker: str, coverage_md: str) -> bool:
    return is_ticker_covered(ticker, coverage_md)


def normalize_region(region: str, exchange: str | None = None) -> str:
    """Map LLM region strings to pipeline region codes."""
    r = (region or "").upper()
    if r in ("HK", "HONG KONG", "HKEX"):
        return "HK"
    if r in ("US", "USA", "UNITED STATES", "NYSE", "NASDAQ"):
        return "US"
    if r in ("CN", "CHINA", "A-SHARE", "H-SHARE", "SHG", "SHE"):
        return "CN"
    if r == "DUAL":
        return "DUAL"
    if exchange:
        return infer_region(exchange.upper())
    return r or "HK"


def normalize_rigor(rigor: str) -> str:
    """Map LLM rigor strings to pipeline rigor codes."""
    r = (rigor or "").lower()
    if r in ("deep", "high", "full"):
        return "deep"
    if r in ("surface", "low", "light", "refresh"):
        return "surface"
    if r in ("targeted", "event"):
        return "targeted"
    return r or "deep"


def normalize_ticker(ticker: str | None, exchange: str | None) -> str | None:
    """Normalize classify ticker to TICKER.EXCHANGE form for lookups."""
    if not ticker:
        return None
    t = ticker.strip().upper()
    if "." in t:
        return t
    ex = (exchange or "").upper()
    if ex in ("HK", "HKEX"):
        return f"{t}.HK"
    if ex in ("US", "NYSE", "NASDAQ"):
        return f"{t}.US"
    if ex in ("SHG", "SHANGHAI"):
        return f"{t}.SHG"
    if ex in ("SHE", "SHENZHEN"):
        return f"{t}.SHE"
    return t


def should_precompute_peer_regression(plan: DispatchPlan) -> bool:
    if not plan.ticker:
        return False
    region = normalize_region(plan.region, plan.exchange)
    rigor = normalize_rigor(plan.rigor)
    task_type = (plan.task_type or "").lower()
    deep_rigor = rigor == "deep" or task_type in ("initiation", "initiate_coverage")
    return region in ("HK", "CN", "DUAL", "US") and deep_rigor


def infer_task_type(task: str) -> str:
    lower = task.lower()
    if "initiat" in lower:
        return "initiation"
    if "refresh" in lower:
        return "refresh"
    if "event" in lower:
        return "event"
    return "other"


def infer_rigor(task_type: str) -> str:
    if task_type == "initiation":
        return "deep"
    if task_type == "refresh":
        return "surface"
    if task_type == "event":
        return "targeted"
    return "deep"


def infer_region(exchange: str | None) -> str:
    if exchange in ("HK",):
        return "HK"
    if exchange in ("US",):
        return "US"
    if exchange in ("SHG", "SHE"):
        return "CN"
    return "HK"


def default_agents_for_region(region: str) -> list[str]:
    agents = ["macro_specialist", "sector_specialist", "valuation_specialist", "risk_specialist"]
    if region == "DUAL":
        agents.extend(["hk_analyst", "china_ah_analyst"])
    elif region == "HK":
        agents.append("hk_analyst")
    elif region == "US":
        agents.append("us_analyst")
    elif region == "CN":
        agents.append("china_ah_analyst")
    return agents


def default_dispatch_plan(task: str, coverage_md: str) -> DispatchPlan:
    ticker = detect_ticker(task)
    _code, exchange = _split_ticker_exchange(ticker)
    task_type = infer_task_type(task)
    region = infer_region(exchange)
    is_covered = bool(ticker and ticker_in_coverage_md(ticker, coverage_md))
    agents = default_agents_for_region(region)
    if is_covered:
        agents = ["coverage_agent", *agents]
    return DispatchPlan(
        ticker=ticker,
        exchange=exchange,
        region=region,
        rigor=infer_rigor(task_type),
        is_covered=is_covered,
        agents_needed=agents,
        task_type=task_type,
        raw_classification="fallback: detect_ticker()",
    )


def parse_dispatch_plan(raw: str, task: str, coverage_md: str) -> DispatchPlan:
    """Parse Director classify JSON; fall back on failure."""
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        plan = default_dispatch_plan(task, coverage_md)
        _log_step("classify_parse", status="fallback", reason="no JSON found")
        return plan
    try:
        data = json.loads(match.group(0))
        ticker = data.get("ticker")
        if isinstance(ticker, str):
            ticker = ticker.upper()
        exchange = data.get("exchange")
        region = str(data.get("region", infer_region(exchange))).upper()
        rigor = str(data.get("rigor", "deep"))
        is_covered = bool(data.get("is_covered", False))
        agents_needed = data.get("agents_needed") or default_agents_for_region(region)
        task_type = str(data.get("task_type", infer_task_type(task)))
        return DispatchPlan(
            ticker=ticker,
            exchange=exchange,
            region=region,
            rigor=rigor,
            is_covered=is_covered,
            agents_needed=list(agents_needed),
            task_type=task_type,
            raw_classification=raw,
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        plan = default_dispatch_plan(task, coverage_md)
        _log_step("classify_parse", status="fallback", reason="JSON parse error")
        return plan


def extract_sector_from_coverage(ticker: str | None, coverage_md: str) -> str:
    if not ticker:
        return "unknown"
    for line in coverage_md.splitlines():
        if ticker.upper() in line.upper() and "|" in line:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 3:
                return parts[2]
    return "unknown"


def summarize_context(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]..."


def build_classify_instructions(
    prompts: dict[str, str],
    coverage_context: str,
    ticker: str | None,
    python_is_covered: bool,
    covered_tickers: list[str],
) -> str:
    watchlist = ", ".join(covered_tickers) if covered_tickers else "(empty)"
    coverage_status = (
        "\n\n## Coverage Status (resolved by runtime — do not override)\n"
        f"Detected ticker: {ticker or 'none detected'}\n"
        f"is_covered: {python_is_covered}\n"
        f"Full active watchlist: {watchlist}\n\n"
        "Do not re-determine is_covered. Use the value above."
    )
    return (
        prompts["lab_md"][:2000]
        + "\n\n---\n\n# Active context (coverage.md)\n\n"
        + prompts["coverage_md"]
        + coverage_status
        + (f"\n\n{coverage_context}" if coverage_context else "")
    )


def build_synthesize_instructions(prompts: dict[str, str]) -> str:
    director_synthesis = extract_md_section(prompts["director"], "4. Synthesis")
    quality_gates = extract_md_section(prompts["lab_md"], "5. Quality Gates")
    if not director_synthesis:
        director_synthesis = prompts["director"]
    return director_synthesis + "\n\n---\n\n" + quality_gates


def _log_analyst_tools(
    analyst_role: str,
    instructions: str,
    config: LabConfig,
    mcp_server: Any | None,
) -> list[str]:
    """Log function-tool names attached to a regional analyst before Runner.run()."""
    agent = build_step_agent(analyst_role, instructions, config, mcp_server)
    names = [getattr(t, "name", type(t).__name__) for t in (agent.tools or [])]
    _log_step("analyst_tools", role=analyst_role, tools=names)
    return names


def _apply_classify_coverage_override(
    plan: DispatchPlan,
    python_is_covered: bool,
) -> DispatchPlan:
    if plan.is_covered != python_is_covered:
        _log_step(
            "classify_override",
            llm_said=plan.is_covered,
            python_resolved=python_is_covered,
            ticker=plan.ticker,
        )
        plan.is_covered = python_is_covered
    if plan.is_covered and "coverage_agent" not in plan.agents_needed:
        plan.agents_needed = ["coverage_agent", *plan.agents_needed]
    return plan


def _apply_classify_agents_override(plan: DispatchPlan) -> DispatchPlan:
    """Enforce agents_needed rules when classify JSON omits required roles."""
    needed = list(plan.agents_needed)
    region = normalize_region(plan.region, plan.exchange)
    rigor = normalize_rigor(plan.rigor)
    task_type = (plan.task_type or "").lower()

    def ensure(role: str) -> None:
        if role not in needed:
            needed.append(role)

    if plan.is_covered:
        ensure("coverage_agent")
    ensure("macro_specialist")

    if region == "DUAL":
        ensure("hk_analyst")
        ensure("china_ah_analyst")
    elif region == "HK":
        ensure("hk_analyst")
    elif region == "US":
        ensure("us_analyst")
    elif region == "CN":
        ensure("china_ah_analyst")

    if task_type in ("initiation", "initiate_coverage") and rigor == "deep":
        for role in SPECIALIST_ORDER:
            ensure(role)
    elif rigor in ("surface", "targeted") or task_type in ("refresh", "surface"):
        needed = [r for r in needed if r != "sector_specialist"]

    if plan.is_covered and "coverage_agent" in needed:
        needed = ["coverage_agent"] + [r for r in needed if r != "coverage_agent"]

    plan.agents_needed = needed
    return plan


def _build_peer_regression_context(result: Any) -> str:
    return (
        "## Phase 1 Pre-Computed Peer Regression\n"
        "The following FactorRegime input has been pre-computed. "
        "Use this as your Phase 1 regression output — do not re-run it. "
        "Proceed directly to Phase 1 synthesis (broker consensus + macro signal "
        "integration) then Phase 2 stock analysis.\n\n"
        f"{json.dumps(asdict(result), indent=2)}"
    )


def _build_peer_regression_tool_instruction(ticker: str | None) -> str:
    peers = DEFAULT_PEERS.get((ticker or "").upper(), [])
    return (
        "## Phase 1 Instruction — REQUIRED FIRST ACTION\n"
        "Before any analysis, call peer_regression_tool with:\n"
        f"  target_ticker: '{ticker}'\n"
        f"  peer_tickers: {peers}\n"
        "  lookback_months: 12\n"
        "Wait for the result. Use it as your Phase 1 regression input. "
        "Do not proceed to Phase 1 synthesis until this tool call returns.\n\n"
    )


def _count_turns(result: Any) -> int:
    items = getattr(result, "new_items", None)
    if items:
        return len(items)
    return 1


async def _run_agent_step(
    role: str,
    instructions: str,
    user_message: str,
    config: LabConfig,
    run_config: Any,
    mcp_server: Any | None,
    max_turns: int,
    tools: list[Any] | None = None,
) -> tuple[str, int, str]:
    from agents import Runner

    agent = build_step_agent(role, instructions, config, mcp_server, tools=tools)
    try:
        result = await Runner.run(
            agent,
            user_message,
            run_config=run_config,
            max_turns=max_turns,
        )
        output = str(result.final_output or "")
        turns = _count_turns(result)
        return output, turns, "ok"
    except Exception as exc:
        return f"[step error: {exc}]", 0, "error"


async def run_pipeline(
    task: str,
    config: LabConfig,
    prompts: dict[str, str],
) -> dict[str, Any]:
    """Programmatic multi-agent research pipeline (Path A)."""
    from agents.extensions.models.litellm_provider import LitellmProvider
    from agents.model_settings import ModelSettings
    from agents.run_config import RunConfig

    configure_provider_environment(config)
    report = boot(config, prompts)

    ticker = detect_ticker(task)
    covered_tickers = get_covered_tickers(prompts["coverage_md"])
    python_is_covered = is_ticker_covered(ticker or "", prompts["coverage_md"])
    coverage_context = ""
    if ticker:
        state = load_coverage_state(ticker)
        coverage_context = build_coverage_context(ticker, state)

    steps: dict[str, Any] = {}
    specialist_outputs: dict[str, str] = {}
    pipeline_status = "completed"
    peer_regression_result: Any | None = None
    use_peer_tool_prompt = False

    max_tokens = resolve_max_output_tokens()
    run_config = RunConfig(
        model_provider=LitellmProvider(),
        model_settings=ModelSettings(max_tokens=max_tokens),
    )

    eodhd = build_eodhd_stdio_server(config)
    async with eodhd:
        # Step 1 — Director classify
        classify_msg = (
            f"Task: {task}\n\n"
            "Return a JSON object with exactly these fields: "
            "ticker, exchange, region, rigor, is_covered, agents_needed, task_type. "
            "agents_needed must be a list drawn from: "
            "[coverage_agent, macro_specialist, us_analyst, hk_analyst, "
            "china_ah_analyst, sector_specialist, valuation_specialist, risk_specialist].\n\n"
            "is_covered: USE THE VALUE PROVIDED IN SYSTEM CONTEXT. "
            "Do not infer from task text. The runtime has already resolved "
            "this from coverage.md.\n\n"
            "agents_needed selection rules:\n"
            "- If is_covered=True: always include coverage_agent first\n"
            "- If region=HK, CN, or DUAL: always include macro_specialist\n"
            "- If task_type=initiation and rigor=deep: include all specialists "
            "(sector_specialist, valuation_specialist, risk_specialist)\n"
            "- If task_type=surface or rigor=surface/targeted: omit sector_specialist\n\n"
            "Do not perform analysis. Do not produce a memo. Return JSON only — no prose."
        )
        classify_out, classify_turns, classify_status = await _run_agent_step(
            "director",
            build_classify_instructions(
                prompts,
                coverage_context,
                ticker,
                python_is_covered,
                covered_tickers,
            ),
            classify_msg,
            config,
            run_config,
            None,
            max_turns=3,
        )
        steps["classify"] = classify_out
        _log_step(
            "classify",
            turns=classify_turns,
            output_chars=len(classify_out),
            status=classify_status,
        )
        if classify_status != "ok":
            return {
                "status": "failed",
                "task": task,
                "dispatch_plan": None,
                "steps": steps,
                "final_memo": "",
                "boot": report.to_dict(),
            }

        plan = parse_dispatch_plan(classify_out, task, prompts["coverage_md"])
        if not plan.ticker:
            plan = default_dispatch_plan(task, prompts["coverage_md"])
            _log_step("classify", status="fallback", reason="empty ticker in plan")
        plan = _apply_classify_coverage_override(plan, python_is_covered)
        plan = _apply_classify_agents_override(plan)
        plan.region = normalize_region(plan.region, plan.exchange)
        if plan.ticker:
            plan.ticker = normalize_ticker(plan.ticker, plan.exchange) or plan.ticker

        # Step 2 — Coverage Agent
        if plan.is_covered and "coverage_agent" in plan.agents_needed:
            coverage_msg = (
                f"Ticker: {plan.ticker}\n"
                f"Trigger: {plan.task_type}\n"
                f"Rigor: {plan.rigor}\n\n"
                "Coverage state loaded below. Produce a ContextInjectionPackage "
                "per your §7 output contract. Return structured output only.\n\n"
                f"{coverage_context or 'No coverage_state files found.'}"
            )
            ctx_out, ctx_turns, ctx_status = await _run_agent_step(
                "coverage_agent",
                prompts["coverage_agent"],
                coverage_msg,
                config,
                run_config,
                None,
                max_turns=5,
            )
            context_package = ctx_out
            steps["coverage_agent"] = ctx_out
            _log_step(
                "coverage_agent",
                turns=ctx_turns,
                output_chars=len(ctx_out),
                status=ctx_status,
            )
            if ctx_status != "ok":
                pipeline_status = "partial"
        else:
            context_package = "No prior coverage — first touch."
            steps["coverage_agent"] = context_package
            _log_step(
                "coverage_agent",
                skipped=True,
                reason="not covered or not in agents_needed",
            )

        # Step 3 — Macro
        sector = extract_sector_from_coverage(plan.ticker, prompts["coverage_md"])
        macro_msg = (
            f"Region: {plan.region}\n"
            f"Sector: {sector}\n"
            f"Rigor: {plan.rigor}\n"
            f"Context: {summarize_context(context_package)}\n\n"
            "Produce a MacroRegimeTag per your §4 output contract. "
            "Return structured output only."
        )
        macro_out, macro_turns, macro_status = await _run_agent_step(
            "macro_specialist",
            prompts["macro_specialist"],
            macro_msg,
            config,
            run_config,
            None,
            max_turns=5,
        )
        macro_tag = macro_out
        steps["macro"] = macro_out
        _log_step("macro", turns=macro_turns, output_chars=len(macro_out), status=macro_status)
        if macro_status != "ok":
            pipeline_status = "partial"

        # Step 4 prep — tool visibility (determines Step 3.5 vs prompt hardening)
        analyst_parts: list[str] = []
        regional_roles = REGIONAL_ROLES.get(plan.region, "hk_analyst")
        if isinstance(regional_roles, str):
            regional_roles = [regional_roles]

        active_analyst_roles = [
            role for role in regional_roles if role in plan.agents_needed
        ]
        peer_tool_visible = False
        if active_analyst_roles:
            probe_role = active_analyst_roles[0]
            probe_tools = _log_analyst_tools(
                probe_role,
                prompts[probe_role],
                config,
                eodhd,
            )
            peer_tool_visible = "peer_regression_tool" in probe_tools

        # Step 3.5 — Peer regression (programmatic; reliable even when model skips tool)
        if should_precompute_peer_regression(plan):
            peer_list = DEFAULT_PEERS.get((plan.ticker or "").upper())
            if peer_list:
                peer_regression_result = run_peer_regression(
                    target_ticker=plan.ticker or "",
                    peer_tickers=peer_list,
                    lookback_months=12,
                )
                steps["peer_regression"] = asdict(peer_regression_result)
                _log_step(
                    "peer_regression",
                    ticker=plan.ticker,
                    n_peers=peer_regression_result.n_peers,
                    confidence=peer_regression_result.confidence,
                    data_gaps=peer_regression_result.data_gaps,
                )
            else:
                _log_step(
                    "peer_regression",
                    skipped=True,
                    reason="no default peers",
                    ticker=plan.ticker,
                )

        # Step 4 — Regional analyst(s), sequential
        for analyst_role in active_analyst_roles:
            if analyst_role != active_analyst_roles[0]:
                _log_analyst_tools(
                    analyst_role,
                    prompts[analyst_role],
                    config,
                    eodhd,
                )
            analyst_prefix = ""
            if peer_regression_result is not None:
                analyst_prefix = _build_peer_regression_context(peer_regression_result) + "\n\n"
            elif peer_tool_visible:
                use_peer_tool_prompt = True
                analyst_prefix = _build_peer_regression_tool_instruction(plan.ticker)
            analyst_msg = (
                f"{analyst_prefix}"
                f"Task: {plan.task_type} on {plan.ticker}\n"
                f"Rigor: {plan.rigor}\n"
                f"MacroRegimeTag: {summarize_context(macro_tag, 1500)}\n"
                f"Prior context: {summarize_context(context_package, 1500)}\n\n"
                "Execute Phase 1 (regime discovery) then Phase 2 (stock analysis). "
                "Return structured output per your §5 output contract: "
                "FactorRegime object + Sections 1-4 content."
            )
            out, turns, status = await _run_agent_step(
                analyst_role,
                prompts[analyst_role],
                analyst_msg,
                config,
                run_config,
                eodhd,
                max_turns=20,
            )
            analyst_parts.append(f"--- {analyst_role} ---\n{out}")
            steps[analyst_role] = out
            _log_step(
                analyst_role,
                turns=turns,
                output_chars=len(out),
                status=status,
            )
            if status != "ok":
                pipeline_status = "partial"

        analyst_output = "\n\n".join(analyst_parts) if analyst_parts else "[no analyst output]"
        steps["analyst"] = analyst_output

        # Step 5 — Specialists
        valuation_output = ""
        for spec_role in SPECIALIST_ORDER:
            if spec_role not in plan.agents_needed:
                continue
            if spec_role == "sector_specialist":
                spec_msg = (
                    f"Ticker: {plan.ticker}\n"
                    f"Region: {plan.region}\n"
                    f"Sector: {sector}\n"
                    f"FactorRegime (from analyst): {summarize_context(analyst_output, 2000)}\n\n"
                    "Return structured output per your §4 output contract."
                )
            elif spec_role == "valuation_specialist":
                spec_msg = (
                    f"FactorRegime: {summarize_context(analyst_output, 1500)}\n"
                    f"Analyst Phase 2 output: {summarize_context(analyst_output, 2500)}\n"
                    f"MacroRegimeTag: {summarize_context(macro_tag, 1000)}\n"
                    f"Rigor: {plan.rigor}\n\n"
                    "Return ValuationOutput per your §4 output contract."
                )
            else:
                spec_msg = (
                    f"FactorRegime: {summarize_context(analyst_output, 1500)}\n"
                    f"ValuationOutput: {summarize_context(valuation_output, 1500)}\n"
                    f"MacroRegimeTag: {summarize_context(macro_tag, 1000)}\n"
                    f"Analyst output: {summarize_context(analyst_output, 2000)}\n\n"
                    "Return ScenarioOutput per your §4 output contract."
                )

            out, turns, status = await _run_agent_step(
                spec_role,
                prompts[spec_role],
                spec_msg,
                config,
                run_config,
                eodhd if spec_role in ("sector_specialist", "valuation_specialist") else None,
                max_turns=8,
            )
            specialist_outputs[spec_role] = out
            if spec_role == "valuation_specialist":
                valuation_output = out
            _log_step(
                spec_role,
                turns=turns,
                output_chars=len(out),
                status=status,
            )
            if status != "ok":
                pipeline_status = "partial"

        steps["specialists"] = specialist_outputs

        # Step 6 — Director synthesize
        spec_block = "\n".join(
            f"--- {role} ---\n{body}" for role, body in specialist_outputs.items()
        )
        synth_msg = (
            f"Ticker: {plan.ticker} | Region: {plan.region}\n"
            f"Task: {plan.task_type} | Rigor: {plan.rigor}\n\n"
            "Sub-agent outputs:\n"
            f"--- CONTEXT PACKAGE ---\n{context_package}\n"
            f"--- MACRO REGIME ---\n{macro_tag}\n"
            f"--- ANALYST OUTPUT (Sections 1-4) ---\n{analyst_output}\n"
            f"--- SPECIALIST OUTPUTS ---\n{spec_block or '[none]'}\n\n"
            "Synthesize these into the final 8-section investment memo per the memo template. "
            f"Apply lab.md §5 quality gates. Stamp with lab.md version and "
            f"coverage.md hash {report.coverage_md_hash}. Return the complete memo."
        )
        memo_out, memo_turns, memo_status = await _run_agent_step(
            "director",
            build_synthesize_instructions(prompts),
            synth_msg,
            config,
            run_config,
            None,
            max_turns=10,
        )
        steps["memo"] = memo_out
        _log_step("memo", turns=memo_turns, output_chars=len(memo_out), status=memo_status)
        if memo_status != "ok":
            pipeline_status = "partial"

    return {
        "status": pipeline_status,
        "task": task,
        "dispatch_plan": asdict(plan),
        "steps": steps,
        "final_memo": memo_out,
        "boot": report.to_dict(),
    }


async def run_director_task(task: str, config: LabConfig, prompts: dict[str, str]) -> dict[str, Any]:
    """Deprecated alias — use run_pipeline()."""
    return await run_pipeline(task, config, prompts)


def _strip_memo_fences(memo_text: str) -> str:
    text = memo_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[^\n]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def parse_memo_sections(memo_text: str) -> dict[str, str]:
    """
    Split final_memo into section_number → content.
    Falls back to {"full": memo_text} if section headers not found.
    """
    text = _strip_memo_fences(memo_text)
    if not text:
        return {"full": ""}

    markers: list[tuple[int, str]] = []
    for num, title in MEMO_SECTION_TITLES:
        pattern = re.compile(
            rf"(?:^|\n)\s*(?:#{1,3}\s*)?{re.escape(num)}\.\s*{re.escape(title)}",
            re.IGNORECASE,
        )
        match = pattern.search(text)
        if match:
            markers.append((match.start(), num))

    if not markers:
        return {"full": text}

    markers.sort(key=lambda item: item[0])
    sections: dict[str, str] = {}
    for idx, (start, num) in enumerate(markers):
        end = markers[idx + 1][0] if idx + 1 < len(markers) else len(text)
        sections[num] = text[start:end].strip()
    return sections or {"full": text}


def extract_memo_header(memo_text: str) -> str:
    """Extract conviction stamp block for parent Slack message."""
    text = _strip_memo_fences(memo_text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return "INVESTMENT MEMO"

    header_lines: list[str] = []
    for ln in lines[:8]:
        header_lines.append(ln)
        if re.search(r"conviction\s*:", ln, re.IGNORECASE):
            break
    if header_lines:
        return "\n".join(header_lines)
    return "\n".join(lines[:3])


def _extract_memo_fields(memo_text: str, result: dict[str, Any]) -> dict[str, str]:
    header = extract_memo_header(memo_text)
    plan = result.get("dispatch_plan") or {}
    ticker = plan.get("ticker") or detect_ticker(result.get("task", "")) or "UNKNOWN"
    region = plan.get("region") or "UNKNOWN"
    boot = result.get("boot") or {}
    coverage_hash = boot.get("coverage_md_hash", "unknown")
    today = date.today().isoformat()

    conviction = "n/a"
    price_target = "n/a"
    upside = "n/a"
    divergence = "n/a"
    for ln in header.splitlines():
        if re.search(r"conviction\s*:", ln, re.IGNORECASE):
            conviction = ln.split(":", 1)[-1].strip()
        if re.search(r"\bPT\s*:", ln, re.IGNORECASE):
            price_target = ln.split(":", 1)[-1].strip()
        if re.search(r"upside\s*:", ln, re.IGNORECASE):
            upside = ln.split(":", 1)[-1].strip()
        if re.search(r"divergence", ln, re.IGNORECASE):
            divergence = ln.split(":", 1)[-1].strip()

    stamp = f"lab.md v0.1.0 | coverage.md {coverage_hash} | {today}"
    return {
        "ticker": str(ticker),
        "region": str(region),
        "stamp": stamp,
        "conviction": conviction,
        "price_target": price_target,
        "upside": upside,
        "divergence": divergence,
    }


def _truncate_slack_text(text: str, limit: int = SLACK_SECTION_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n\n...[truncated]..."


def _strip_bot_mention(text: str) -> str:
    return re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()


def write_locked_section(ticker: str, section_num: str, content: str) -> None:
    """Append locked section content to coverage_state/[TICKER]/locked_sections.md."""
    state_dir = COVERAGE_STATE / ticker
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / COVERAGE_STATE_FILES["locked_sections"]
    stamp = date.today().isoformat()
    entry = f"\n\n## Section {section_num} (locked {stamp})\n\n{content.strip()}\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(entry)


async def handle_slack_message(
    task: str,
    thread_ts: str | None,
    feedback_context: str | None,
    channel_id: str,
    client: Any,
    config: LabConfig,
    prompts: dict[str, str],
    state: SlackBridgeState,
) -> None:
    """Route a Slack message to run_pipeline() and post the memo back to Slack."""
    display_task = task if len(task) <= 60 else task[:57] + "..."
    ack = await client.chat_postMessage(
        channel=channel_id,
        text=(
            f"🔬 Research Lab: running pipeline for '{display_task}'\n"
            "This takes 60-120 seconds."
        ),
        thread_ts=thread_ts,
    )
    ack_ts = ack["ts"]

    pipeline_task = task
    if feedback_context:
        pipeline_task = f"[Feedback context]\n{feedback_context}\n\n{task}"

    ticker_probe = detect_ticker(pipeline_task)
    if ticker_probe and ticker_probe in state.compliance_holds:
        await client.chat_update(
            channel=channel_id,
            ts=ack_ts,
            text=(
                f"⚠️ Research on {ticker_probe} is paused pending compliance review. "
                "Reply with an explicit resume instruction to continue."
            ),
        )
        return

    try:
        result = await run_pipeline(pipeline_task, config, prompts)
    except Exception as exc:
        await client.chat_update(
            channel=channel_id,
            ts=ack_ts,
            text=f"❌ Pipeline failed: {exc}\n```{traceback.format_exc()[-1500:]}```",
        )
        return

    state.last_task = pipeline_task
    plan = result.get("dispatch_plan") or {}
    if plan.get("ticker"):
        state.last_ticker = str(plan["ticker"])

    if result.get("status") == "failed" or not result.get("final_memo"):
        steps = result.get("steps") or {}
        step_summary = {
            key: (len(val) if isinstance(val, str) else type(val).__name__)
            for key, val in steps.items()
        }
        await client.chat_update(
            channel=channel_id,
            ts=ack_ts,
            text=(
                f"❌ Pipeline {result.get('status', 'failed')} — no memo produced.\n"
                f"```{json.dumps(step_summary, indent=2)}```"
            ),
        )
        return

    memo_text = _strip_memo_fences(str(result["final_memo"]))
    fields = _extract_memo_fields(memo_text, result)
    parent_text = (
        f"INVESTMENT MEMO — {fields['ticker']} {fields['region']}\n"
        f"Stamped: {fields['stamp']}\n"
        f"─────────────────────────────────\n"
        f"Conviction: {fields['conviction']} | PT: {fields['price_target']} | "
        f"Upside: {fields['upside']}\n"
        f"Divergence: {fields['divergence']}"
    )
    parent = await client.chat_postMessage(
        channel=channel_id,
        text=_truncate_slack_text(parent_text, 3900),
        thread_ts=thread_ts,
    )
    memo_ts = parent["ts"]

    sections = parse_memo_sections(memo_text)
    section_ts_map: dict[str, str] = {}
    if "full" in sections:
        sec_resp = await client.chat_postMessage(
            channel=channel_id,
            text=_truncate_slack_text(sections["full"]),
            thread_ts=memo_ts,
        )
        section_ts_map["full"] = sec_resp["ts"]
        state.section_content_by_ts[sec_resp["ts"]] = sections["full"]
    else:
        title_by_num = dict(MEMO_SECTION_TITLES)
        for num, body in sections.items():
            title = title_by_num.get(num, f"Section {num}")
            sec_resp = await client.chat_postMessage(
                channel=channel_id,
                text=_truncate_slack_text(f"*{num}. {title}*\n\n{body.strip()}"),
                thread_ts=memo_ts,
            )
            sec_ts = sec_resp["ts"]
            section_ts_map[num] = sec_ts
            state.section_num_by_ts[sec_ts] = num
            state.section_content_by_ts[sec_ts] = body
            state.memo_parent_by_section_ts[sec_ts] = memo_ts

    state.memo_meta_by_ts[memo_ts] = {
        "ticker": fields["ticker"],
        "task": pipeline_task,
        "section_ts": section_ts_map,
    }

    await client.chat_update(channel=channel_id, ts=ack_ts, text="✅ Done.")


def _validate_slack_env() -> tuple[str, str, str]:
    load_dotenv(ROOT / ".env")
    load_dotenv()
    bot = os.getenv("SLACK_BOT_TOKEN", "").strip()
    app_token = os.getenv("SLACK_APP_TOKEN", "").strip()
    channel = os.getenv("SLACK_CHANNEL_ID", "").strip()
    missing = [
        name
        for name, val in (
            ("SLACK_BOT_TOKEN", bot),
            ("SLACK_APP_TOKEN", app_token),
            ("SLACK_CHANNEL_ID", channel),
        )
        if not val
    ]
    if missing:
        raise LabConfigError(
            "Slack mode requires: " + ", ".join(missing) + ". Local CLI works without Slack."
        )
    return bot, app_token, channel


def _register_slack_handlers(
    app: Any,
    config: LabConfig,
    prompts: dict[str, str],
    channel_id: str,
    state: SlackBridgeState,
) -> None:
    """Register Slack Socket Mode event handlers on AsyncApp."""

    async def _dispatch(coro: Any) -> None:
        try:
            await coro
        except Exception as exc:
            print(json.dumps({"step": "slack_handler_error", "error": str(exc)}), flush=True)

    async def _run_task(
        task: str,
        client: Any,
        thread_ts: str | None = None,
        feedback_context: str | None = None,
    ) -> None:
        await handle_slack_message(
            task=task,
            thread_ts=thread_ts,
            feedback_context=feedback_context,
            channel_id=channel_id,
            client=client,
            config=config,
            prompts=prompts,
            state=state,
        )

    @app.event("app_mention")
    async def on_app_mention(event: dict[str, Any], client: Any) -> None:
        if event.get("bot_id"):
            return
        text = _strip_bot_mention(event.get("text", ""))
        if not text:
            return
        asyncio.create_task(_dispatch(_run_task(text, client)))

    @app.event("message")
    async def on_message(event: dict[str, Any], client: Any) -> None:
        if event.get("bot_id") or event.get("subtype"):
            return
        if event.get("channel") != channel_id:
            return

        thread_ts = event.get("thread_ts")
        msg_ts = event.get("ts")
        text = event.get("text", "").strip()
        if not text:
            return

        # Thread reply → feedback routing (Handler 2)
        if thread_ts and thread_ts != msg_ts:
            section_num = state.section_num_by_ts.get(thread_ts)
            if not section_num:
                for meta in state.memo_meta_by_ts.values():
                    for num, sec_ts in (meta.get("section_ts") or {}).items():
                        if thread_ts == sec_ts:
                            section_num = num
                            break
                    if section_num:
                        break
            section_ctx = f"Section {section_num}" if section_num else "memo thread"
            feedback = f"[{section_ctx}] {text}"
            asyncio.create_task(
                _dispatch(
                    _run_task(
                        f"Re-run with feedback: {text}",
                        client,
                        thread_ts=thread_ts,
                        feedback_context=feedback,
                    )
                )
            )
            return

        # Top-level channel message (Handler 1) — ignore @mentions (app_mention handles)
        if "<@" in text:
            return
        asyncio.create_task(_dispatch(_run_task(text, client)))

    @app.event("reaction_added")
    async def on_reaction(event: dict[str, Any], client: Any) -> None:
        reaction = event.get("reaction", "")
        item = event.get("item") or {}
        if item.get("type") != "message":
            return
        msg_ts = item.get("ts")
        if not msg_ts:
            return

        section_num = state.section_num_by_ts.get(msg_ts)
        parent_ts = state.memo_parent_by_section_ts.get(msg_ts)
        memo_meta = state.memo_meta_by_ts.get(msg_ts) or state.memo_meta_by_ts.get(
            parent_ts or "", {}
        )
        if not section_num and not memo_meta:
            return

        ticker = memo_meta.get("ticker") or state.last_ticker or "UNKNOWN"
        content = state.section_content_by_ts.get(msg_ts, "")

        if reaction in ("thumbsup", "+1"):
            return
        if reaction in ("thumbsdown", "-1"):
            await client.chat_postMessage(
                channel=channel_id,
                text="Please reply in this thread with feedback to re-run this section.",
                thread_ts=msg_ts,
            )
            return
        if reaction == "arrows_counterclockwise":
            sec_label = section_num or "full memo"
            asyncio.create_task(
                _dispatch(
                    _run_task(
                        f"Re-run section {sec_label} from scratch for {ticker}",
                        client,
                        thread_ts=msg_ts,
                        feedback_context=f"Re-run section {sec_label} from scratch.",
                    )
                )
            )
            return
        if reaction == "pushpin":
            if not detect_ticker(str(ticker)) and not detect_ticker(content):
                print(
                    json.dumps(
                        {
                            "step": "lock_skip",
                            "reason": "detect_ticker failed",
                            "ticker": ticker,
                        }
                    ),
                    flush=True,
                )
                return
            lock_ticker = detect_ticker(str(ticker)) or detect_ticker(content) or str(ticker)
            if section_num and content:
                try:
                    write_locked_section(lock_ticker, section_num, content)
                    await client.chat_postMessage(
                        channel=channel_id,
                        text=f"Section {section_num} locked for {lock_ticker}.",
                        thread_ts=msg_ts,
                    )
                except OSError as exc:
                    print(json.dumps({"step": "lock_error", "error": str(exc)}), flush=True)
            return
        if reaction == "warning":
            state.compliance_holds.add(str(ticker))
            await client.chat_postMessage(
                channel=channel_id,
                text=(
                    f"Compliance flag noted. Research on {ticker} paused pending review."
                ),
                thread_ts=msg_ts,
            )

    @app.command("/rerun")
    async def cmd_rerun(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        section = (body.get("text") or "").strip()
        task = state.last_task or "Re-run last research task"
        if section:
            task = f"Re-run section {section}: {task}"
        asyncio.create_task(
            _dispatch(
                _run_task(
                    task,
                    client,
                    thread_ts=body.get("channel_id"),
                    feedback_context=section or None,
                )
            )
        )

    @app.command("/rerun-all")
    async def cmd_rerun_all(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        ticker = state.last_ticker or "last ticker"
        task = state.last_task or f"Re-run full pipeline for {ticker}"
        asyncio.create_task(_dispatch(_run_task(task, client)))

    @app.command("/lock")
    async def cmd_lock(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        section = (body.get("text") or "1").strip()
        ticker = state.last_ticker
        if not ticker:
            await client.chat_postMessage(
                channel=channel_id,
                text="No ticker available to lock. Run a memo first.",
            )
            return
        content = ""
        for ts, num in state.section_num_by_ts.items():
            if num == section:
                content = state.section_content_by_ts.get(ts, "")
                break
        if not content:
            await client.chat_postMessage(
                channel=channel_id,
                text=f"Section {section} content not found in memory.",
            )
            return
        try:
            write_locked_section(ticker, section, content)
            await client.chat_postMessage(
                channel=channel_id,
                text=f"Section {section} locked for {ticker}.",
            )
        except OSError as exc:
            print(json.dumps({"step": "lock_error", "error": str(exc)}), flush=True)

    @app.command("/macro")
    async def cmd_macro(ack: Any, body: dict[str, Any], client: Any) -> None:
        await ack()
        feedback = (body.get("text") or "").strip()
        ticker = state.last_ticker or "UNKNOWN"
        task = f"Re-run macro analysis for {ticker}"
        if feedback:
            task += f" with feedback: {feedback}"
        asyncio.create_task(
            _dispatch(
                _run_task(
                    task,
                    client,
                    feedback_context=feedback or None,
                )
            )
        )


async def _run_slack_async(config: LabConfig, prompts: dict[str, str]) -> None:
    from slack_bolt.async_app import AsyncApp
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

    bot_token, app_token, channel_id = _validate_slack_env()
    state = SlackBridgeState()
    app = AsyncApp(token=bot_token)
    _register_slack_handlers(app, config, prompts, channel_id, state)
    handler = AsyncSocketModeHandler(app, app_token)
    print(
        json.dumps({"status": "slack_bridge_running", "channel": channel_id}),
        flush=True,
    )
    await handler.start_async()


def run_slack_mode() -> int:
    """Slack Socket Mode bridge per SPEC §7."""
    try:
        bot_token, _app_token, _channel = _validate_slack_env()
    except LabConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        config = load_config()
        prompts = load_prompt_registry()
    except LabConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    if not bot_token.startswith("xoxb-"):
        print("Warning: SLACK_BOT_TOKEN should start with xoxb-", file=sys.stderr)

    try:
        asyncio.run(_run_slack_async(config, prompts))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"Slack bridge error: {exc}", file=sys.stderr)
        return 1


def test_slack_wiring() -> int:
    """Smoke test Slack wiring without live tokens."""
    from slack_bolt.async_app import AsyncApp

    sample_memo = (
        "INVESTMENT MEMO — 9988.HK HK\n"
        "Stamped: lab.md v0.1.0 | coverage.md abc123 | 2026-05-28\n"
        "─────────────────────────────────\n"
        "Conviction: HOLD | PT: HK$120 | Upside: 5%\n"
        "1. Investment Thesis\nBullet one.\n"
        "2. Factor Regime\nRegime text.\n"
        "3. Fundamental Snapshot\nSnapshot text.\n"
    )
    sections = parse_memo_sections(sample_memo)
    header = extract_memo_header(sample_memo)

    app = AsyncApp(token="xoxb-test-token")
    state = SlackBridgeState()
    config = LabConfig(
        model_director="test",
        model_analyst="test",
        model_specialist="test",
        model_coverage="test",
    )
    prompts = {"coverage_md": ""}
    _register_slack_handlers(app, config, prompts, "C00000000", state)

    listener_types = sorted(
        {
            getattr(getattr(listener, "ack_function", None), "__name__", "")
            for listener in app._async_listeners
        }
    )
    handler_names = [n for n in listener_types if n]

    print(json.dumps({"parse_memo_sections": sections}, indent=2))
    print(json.dumps({"extract_memo_header": header}, indent=2))
    print(
        json.dumps(
            {
                "handlers_registered": len(app._async_listeners),
                "handler_names": handler_names,
                "status": "slack_wiring_ok",
            },
            indent=2,
        )
    )
    return 0


async def run_cli(task: str) -> int:
    """Local non-Slack entrypoint."""
    try:
        config = load_config()
        prompts = load_prompt_registry()
    except LabConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    try:
        output = await run_pipeline(task, config, prompts)
        print(json.dumps(output, indent=2, default=str))
        return 0
    except LabConfigError as exc:
        print(f"Boot error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Honest failure after boot — surface boot state + error.
        report = boot(config, prompts)
        payload = {
            "boot": report.to_dict(),
            "task": task,
            "status": "director_run_failed",
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
        print(json.dumps(payload, indent=2, default=str), file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Research Lab boot script (Director entrypoint)",
    )
    parser.add_argument(
        "task",
        nargs="*",
        help='Research task for the Director, e.g. "Initiate coverage on 9988 HK"',
    )
    parser.add_argument(
        "--slack",
        action="store_true",
        help="Start Slack Socket Mode bridge",
    )
    parser.add_argument(
        "--test-slack",
        action="store_true",
        help="Run Slack wiring smoke test (no live tokens)",
    )
    args = parser.parse_args()

    if args.test_slack:
        return test_slack_wiring()

    if args.slack:
        return run_slack_mode()

    if not args.task:
        parser.print_help()
        print(
            "\nExample:\n  python lab.py \"Initiate coverage on 9988 HK\"",
            file=sys.stderr,
        )
        return 1

    task = " ".join(args.task)
    return asyncio.run(run_cli(task))


if __name__ == "__main__":
    sys.exit(main())
