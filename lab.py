#!/usr/bin/env python3
"""Research Lab boot script v0.1.

Loads doctrine and prompts, resolves per-role models, instantiates agents,
registers MCP capabilities, and routes tasks to the Director entrypoint.

Local CLI (no Discord required):
    python lab.py "Initiate coverage on 9988 HK"

Discord bridge:
    python lab.py --discord
    python lab.py --test-discord
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Awaitable, Callable
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

DISCORD_MESSAGE_LIMIT = 1900

# In-memory Discord bridge state (resets on bot restart).
_last_task: str | None = None
_last_ticker: str | None = None

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
    discord_bot_token: str | None = None
    discord_guild_id: str | None = None
    discord_channel_id: str | None = None
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
        discord_bot_token=os.getenv("DISCORD_BOT_TOKEN", "").strip() or None,
        discord_guild_id=os.getenv("DISCORD_GUILD_ID", "").strip() or None,
        discord_channel_id=os.getenv("DISCORD_CHANNEL_ID", "").strip() or None,
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

    # Agents SDK tracing posts to OpenAI's trace API; silence when not using OpenAI.
    if not config.openai_api_key:
        os.environ.setdefault("OPENAI_AGENTS_DISABLE_TRACING", "true")
        try:
            from agents import set_tracing_disabled

            set_tracing_disabled(True)
        except ImportError:
            pass

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


PIPELINE_STEP_LABELS: dict[str, str] = {
    "classify": "Director — classify",
    "coverage_agent": "Coverage Agent",
    "macro": "Macro Analyst",
    "peer_regression": "Peer regression",
    "us_analyst": "US Analyst",
    "hk_analyst": "HK Analyst",
    "china_ah_analyst": "China A/H Analyst",
    "sector_specialist": "Sector Expert",
    "valuation_specialist": "Valuation",
    "risk_specialist": "Risk & Scenarios",
    "memo": "Director — synthesize memo",
}


def build_pipeline_step_queue(plan: DispatchPlan) -> list[str]:
    """Ordered pipeline steps for progress display."""
    steps = ["classify"]
    if plan.is_covered and "coverage_agent" in plan.agents_needed:
        steps.append("coverage_agent")
    steps.append("macro")
    if should_precompute_peer_regression(plan):
        steps.append("peer_regression")
    regional_roles = REGIONAL_ROLES.get(plan.region, "hk_analyst")
    if isinstance(regional_roles, str):
        regional_roles = [regional_roles]
    for role in regional_roles:
        if role in plan.agents_needed:
            steps.append(role)
    for spec_role in SPECIALIST_ORDER:
        if spec_role in plan.agents_needed:
            steps.append(spec_role)
    steps.append("memo")
    return steps


ProgressFn = Callable[[str, str, dict[str, Any]], Awaitable[None]]


async def _pipeline_progress(
    on_progress: ProgressFn | None,
    step: str,
    event: str,
    **meta: Any,
) -> None:
    if on_progress is None:
        return
    try:
        await on_progress(step, event, meta)
    except Exception:
        pass


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
    on_progress: ProgressFn | None = None,
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
        await _pipeline_progress(on_progress, "classify", "start")
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
            await _pipeline_progress(
                on_progress,
                "classify",
                "done",
                status=classify_status,
                turns=classify_turns,
            )
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

        await _pipeline_progress(
            on_progress,
            "pipeline",
            "queue",
            steps=build_pipeline_step_queue(plan),
        )
        await _pipeline_progress(
            on_progress,
            "classify",
            "done",
            status=classify_status,
            turns=classify_turns,
            ticker=plan.ticker,
            region=plan.region,
            rigor=plan.rigor,
            task_type=plan.task_type,
        )

        # Step 2 — Coverage Agent
        if plan.is_covered and "coverage_agent" in plan.agents_needed:
            await _pipeline_progress(on_progress, "coverage_agent", "start")
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
            await _pipeline_progress(
                on_progress,
                "coverage_agent",
                "done",
                status=ctx_status,
                turns=ctx_turns,
            )
        else:
            context_package = "No prior coverage — first touch."
            steps["coverage_agent"] = context_package
            _log_step(
                "coverage_agent",
                skipped=True,
                reason="not covered or not in agents_needed",
            )

        # Step 3 — Macro
        await _pipeline_progress(on_progress, "macro", "start")
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
        await _pipeline_progress(
            on_progress,
            "macro",
            "done",
            status=macro_status,
            turns=macro_turns,
        )

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
            await _pipeline_progress(on_progress, "peer_regression", "start")
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
                await _pipeline_progress(
                    on_progress,
                    "peer_regression",
                    "done",
                    status="ok",
                    n_peers=peer_regression_result.n_peers,
                    confidence=peer_regression_result.confidence,
                )
            else:
                _log_step(
                    "peer_regression",
                    skipped=True,
                    reason="no default peers",
                    ticker=plan.ticker,
                )
                await _pipeline_progress(
                    on_progress,
                    "peer_regression",
                    "done",
                    skipped=True,
                    reason="no default peers",
                )

        # Step 4 — Regional analyst(s), sequential
        for analyst_role in active_analyst_roles:
            await _pipeline_progress(on_progress, analyst_role, "start")
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
            await _pipeline_progress(
                on_progress,
                analyst_role,
                "done",
                status=status,
                turns=turns,
            )

        analyst_output = "\n\n".join(analyst_parts) if analyst_parts else "[no analyst output]"
        steps["analyst"] = analyst_output

        # Step 5 — Specialists
        valuation_output = ""
        for spec_role in SPECIALIST_ORDER:
            if spec_role not in plan.agents_needed:
                continue
            await _pipeline_progress(on_progress, spec_role, "start")
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
            await _pipeline_progress(
                on_progress,
                spec_role,
                "done",
                status=status,
                turns=turns,
            )

        steps["specialists"] = specialist_outputs

        # Step 6 — Director synthesize
        await _pipeline_progress(on_progress, "memo", "start")
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
        await _pipeline_progress(
            on_progress,
            "memo",
            "done",
            status=memo_status,
            turns=memo_turns,
        )

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
    """Extract conviction stamp block for Discord parent message (~first 5 lines)."""
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
        return "\n".join(header_lines[:5])
    return "\n".join(lines[:5])


def format_discord_message(text: str, max_len: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """Split text into chunks ≤ max_len for Discord's 2000-char message limit."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, max_len)
        if split_at <= 0:
            split_at = max_len
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip("\n")
    return chunks or [text[:max_len]]


def _write_locked_section(ticker: str, section_ref: str) -> None:
    """
    Append locked section reference to coverage_state/[TICKER]/locked_sections.md.
    Creates file if not exists. Never raises.
    """
    try:
        path = COVERAGE_STATE / ticker / COVERAGE_STATE_FILES["locked_sections"]
        if not path.parent.exists():
            _log_step("lock_skip", reason="coverage_state dir missing", ticker=ticker)
            return
        stamp = date.today().isoformat()
        entry = (
            f"\n## Locked {stamp} — {section_ref}\n"
            "_Locked via Discord /lock command._\n"
        )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(entry)
    except OSError as exc:
        _log_step("lock_error", error=str(exc), ticker=ticker)


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


def _effective_text_channel(channel: Any) -> Any | None:
    """Map an interaction/message channel to the TextChannel used for posting."""
    import discord

    if isinstance(channel, discord.Thread):
        parent = channel.parent
        if isinstance(parent, discord.TextChannel):
            return parent
        return None
    if isinstance(channel, discord.TextChannel):
        return channel
    return None


def _app_can_post_via_interaction(perms: Any) -> bool:
    """Whether Discord allows interaction follow-up messages in this channel."""
    if perms.administrator:
        return True
    return bool(perms.send_messages)


def _member_can_post(perms: Any) -> bool:
    """Permissions required for channel.send() (REST API — not app_permissions)."""
    if perms.administrator:
        return True
    return bool(perms.view_channel and perms.send_messages)


def _perms_can_create_threads(perms: Any) -> bool:
    return bool(
        perms.administrator
        or perms.manage_threads
        or perms.create_public_threads
    )


def _format_discord_perm_flags(perms: Any) -> str:
    labels = (
        ("view_channel", "View Channel"),
        ("send_messages", "Send Messages"),
        ("create_public_threads", "Create Public Threads"),
        ("send_messages_in_threads", "Send Messages in Threads"),
        ("administrator", "Administrator"),
    )
    lines: list[str] = []
    for attr, label in labels:
        ok = bool(getattr(perms, attr, False))
        lines.append(f"{'✅' if ok else '❌'} {label}")
    return "\n".join(lines)


async def _ensure_bot_member(guild: Any, client: Any) -> Any | None:
    me = guild.me
    if me is not None:
        return me
    try:
        return await guild.fetch_member(client.user.id)
    except Exception:
        return None


async def _channel_permissions_for_bot(
    channel: Any, client: Any, precomputed: Any | None = None
) -> Any | None:
    import discord

    if not isinstance(channel, discord.TextChannel):
        return None
    if precomputed is not None:
        return precomputed
    me = await _ensure_bot_member(channel.guild, client)
    if me is None:
        return None
    return channel.permissions_for(me)


def _missing_access_setup_hint() -> str:
    return (
        "**403 Missing Access (50001)** — the bot cannot see or post in this channel.\n\n"
        "Fix in Discord:\n"
        "1. **Category** — Right-click the category for #research-lab → "
        "Edit Category → Permissions → add your **bot role** → enable **View Channel**.\n"
        "2. **Channel** — Right-click #research-lab → Edit Channel → Permissions → "
        "bot role → enable **View Channel**, **Send Messages**, "
        "**Create Public Threads**, **Send Messages in Threads**.\n"
        "3. **Role order** — Server Settings → Roles → drag the bot role **above** "
        "roles that deny access.\n"
        "4. Restart: `python lab.py --discord`\n\n"
        "Private channels need the bot allowed on the **category or channel** — "
        "server-wide role permissions alone are not enough."
    )


async def _resolve_slash_channel(
    client: Any,
    guild_id: int,
    configured_channel_id: int,
    interaction: Any,
) -> Any | None:
    """Channel target for slash commands (interaction webhook or member perms)."""
    eff = _effective_text_channel(interaction.channel)
    if eff is None:
        return None
    app_perms = getattr(interaction, "app_permissions", None)
    if app_perms is not None and _app_can_post_via_interaction(app_perms):
        return eff
    member_perms = await _channel_permissions_for_bot(eff, client)
    if member_perms is not None and _member_can_post(member_perms):
        return eff
    return await _resolve_text_channel(
        client, guild_id, configured_channel_id, interaction=interaction
    )


async def _discord_post(
    content: str,
    *,
    interaction: Any | None = None,
    channel: Any | None = None,
    thread: Any | None = None,
    wait: bool = True,
) -> Any:
    """Post a message via interaction follow-up or channel/thread send."""
    text = content[:1990]
    if interaction is not None:
        return await interaction.followup.send(text, wait=wait, ephemeral=False)
    if thread is not None:
        return await thread.send(text)
    if channel is not None:
        return await channel.send(text)
    raise ValueError("No Discord post target")


async def _discord_edit(message: Any, content: str) -> None:
    await message.edit(content=content[:1990])


async def _create_memo_thread(
    channel: Any,
    parent_msg: Any,
    thread_name: str,
    *,
    use_interaction: bool,
) -> Any | None:
    """Create a public thread from the memo header message."""
    import discord

    try:
        if use_interaction and isinstance(channel, discord.TextChannel):
            return await channel.create_thread(
                name=thread_name[:100],
                message=parent_msg,
                auto_archive_duration=10080,
            )
        return await parent_msg.create_thread(
            name=thread_name[:100],
            auto_archive_duration=10080,
        )
    except (discord.Forbidden, discord.HTTPException, ValueError):
        return None


async def _resolve_text_channel(
    client: Any,
    guild_id: int,
    configured_channel_id: int,
    interaction: Any | None = None,
) -> Any | None:
    """Resolve a TextChannel the bot can post to via the REST API."""
    import discord

    candidates: list[Any] = []
    if interaction is not None and interaction.channel is not None:
        eff = _effective_text_channel(interaction.channel)
        if eff is not None:
            candidates.append(eff)

    guild = client.get_guild(guild_id)
    if guild is not None:
        candidates.append(guild.get_channel(configured_channel_id))
    candidates.append(client.get_channel(configured_channel_id))

    seen: set[int] = set()
    for ch in candidates:
        if ch is None or not isinstance(ch, discord.TextChannel):
            continue
        if ch.id in seen:
            continue
        seen.add(ch.id)
        perms = await _channel_permissions_for_bot(ch, client)
        if perms is not None and _member_can_post(perms):
            return ch

    try:
        fetched = await client.fetch_channel(configured_channel_id)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        fetched = None
    if fetched is not None and isinstance(fetched, discord.TextChannel):
        perms = await _channel_permissions_for_bot(fetched, client)
        if perms is not None and _member_can_post(perms):
            return fetched
    return None


async def _channel_permission_report(
    client: Any,
    guild_id: int,
    configured_channel_id: int,
    interaction: Any | None = None,
    *,
    error_context: bool = False,
) -> str:
    """Live permission flags for Discord channel setup."""
    import discord

    can_send = False
    via_interaction = False
    member_perms = None
    app_perms = None
    eff = None
    if interaction is not None and interaction.channel is not None:
        eff = _effective_text_channel(interaction.channel)
        app_perms = getattr(interaction, "app_permissions", None)
        if app_perms is not None and _app_can_post_via_interaction(app_perms):
            can_send = True
            via_interaction = True
        if eff is not None:
            member_perms = await _channel_permissions_for_bot(eff, client)
            if member_perms is not None and _member_can_post(member_perms):
                can_send = True
    if not can_send:
        ch = await _resolve_text_channel(
            client, guild_id, configured_channel_id, interaction=interaction
        )
        can_send = ch is not None

    if error_context:
        lines = [
            f"Bot cannot post in channel `{configured_channel_id}` "
            "(DISCORD_CHANNEL_ID).",
            "",
        ]
    else:
        if not can_send:
            status = "❌ Bot cannot send."
        elif via_interaction and member_perms is not None and not _member_can_post(
            member_perms
        ):
            status = (
                "✅ Bot can respond to slash commands (interaction webhook). "
                "Grant **View Channel** for thread replies."
            )
        else:
            status = "✅ Bot can send here."
        lines = [
            status,
            f"Configured channel: `{configured_channel_id}` (DISCORD_CHANNEL_ID).",
            "",
        ]

    if interaction is not None and interaction.channel is not None:
        invoked_id = interaction.channel.id
        eff = _effective_text_channel(interaction.channel)
        lines.append(
            f"Command invoked in channel `{invoked_id}`"
            + (f" (post target `{eff.id}`)" if eff is not None else "")
            + "."
        )
        if eff is not None and eff.id != configured_channel_id:
            lines.append(
                "⚠️ Channel ID mismatch — update DISCORD_CHANNEL_ID in `.env` "
                "to match #research-lab, or run `/research` from that channel."
            )
        app_perms = getattr(interaction, "app_permissions", None)
        if app_perms is not None:
            lines.extend(
                [
                    "",
                    "Discord app permissions (used for slash-command responses):",
                    _format_discord_perm_flags(app_perms),
                ]
            )
            if app_perms.send_messages and member_perms is not None and not _member_can_post(
                member_perms
            ):
                lines.append(
                    "ℹ️ Slash commands can post via interaction webhook without "
                    "**View Channel** on the bot role. Thread replies still need it."
                )
        if eff is not None and member_perms is not None:
            lines.extend(
                [
                    "",
                    f"Member permissions in `{eff.id}` (required for posting):",
                    _format_discord_perm_flags(member_perms),
                ]
            )
        elif eff is not None:
            member_perms = await _channel_permissions_for_bot(eff, client)
            if member_perms is not None:
                lines.extend(
                    [
                        "",
                        f"Member permissions in `{eff.id}` (required for posting):",
                        _format_discord_perm_flags(member_perms),
                    ]
                )

    guild = client.get_guild(guild_id)
    configured = None
    if guild is not None:
        configured = guild.get_channel(configured_channel_id)
    if configured is None:
        configured = client.get_channel(configured_channel_id)
    if configured is None:
        try:
            configured = await client.fetch_channel(configured_channel_id)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            configured = None

    if isinstance(configured, discord.TextChannel):
        cfg_perms = await _channel_permissions_for_bot(configured, client)
        if cfg_perms is not None:
            lines.extend(
                [
                    "",
                    f"Member role permissions in configured channel `{configured.id}`:",
                    _format_discord_perm_flags(cfg_perms),
                ]
            )
    elif configured is None:
        lines.extend(
            [
                "",
                "Configured channel is not visible to the bot (wrong ID, private "
                "channel, or missing **View Channel** on the channel/category).",
            ]
        )

    if not can_send:
        lines.extend(
            [
                "",
                _missing_access_setup_hint(),
            ]
        )
    return "\n".join(lines)


async def _channel_access_hint(
    client: Any,
    guild_id: int,
    configured_channel_id: int,
    interaction: Any | None = None,
) -> str:
    return await _channel_permission_report(
        client,
        guild_id,
        configured_channel_id,
        interaction=interaction,
        error_context=True,
    )


def _channel_permission_hint(channel_id: int) -> str:
    return (
        f"Bot cannot post in channel {channel_id}.\n\n"
        f"{_missing_access_setup_hint()}"
    )


class DiscordProgressBoard:
    """Live pipeline status rendered by editing a single Discord message."""

    def __init__(self, ack: Any, task_display: str) -> None:
        self.ack = ack
        self.task = task_display
        self.started = time.monotonic()
        self.queue: list[str] = []
        self.completed: dict[str, str] = {}
        self.active: str | None = None

    def _label(self, step: str) -> str:
        return PIPELINE_STEP_LABELS.get(step, step.replace("_", " ").title())

    def set_queue(self, steps: list[str]) -> None:
        self.queue = steps

    async def mark_start(self, step: str) -> None:
        self.active = step
        await self.render()

    async def mark_done(self, step: str, **meta: Any) -> None:
        if meta.get("skipped"):
            icon = "⏭️"
        elif meta.get("status") == "ok":
            icon = "✅"
        else:
            icon = "⚠️"
        detail = self._format_detail(step, meta)
        self.completed[step] = f"{icon} {self._label(step)}{detail}"
        if self.active == step:
            self.active = None
        await self.render()

    def _format_detail(self, step: str, meta: dict[str, Any]) -> str:
        if step == "classify" and meta.get("ticker"):
            parts = [str(meta["ticker"])]
            if meta.get("region"):
                parts.append(str(meta["region"]))
            summary = " · ".join(parts)
            if meta.get("rigor"):
                summary += f" · {meta['rigor']}"
            return f" — {summary}"
        if step == "peer_regression":
            if meta.get("skipped"):
                return f" — skipped ({meta.get('reason', 'n/a')})"
            return (
                f" — {meta.get('n_peers', 0)} peers · "
                f"{meta.get('confidence', '?')} confidence"
            )
        if meta.get("skipped"):
            return f" — skipped ({meta.get('reason', 'n/a')})"
        if meta.get("turns"):
            return f" — {meta['turns']} turns"
        if meta.get("status") and meta.get("status") != "ok":
            return f" — {meta['status']}"
        return ""

    async def render(self) -> None:
        elapsed = int(time.monotonic() - self.started)
        mins, secs = divmod(elapsed, 60)
        lines = [
            f"🔬 **Research:** `{self.task}`",
            "─" * 30,
        ]
        if self.queue:
            for step in self.queue:
                if step in self.completed:
                    lines.append(self.completed[step])
                elif step == self.active:
                    lines.append(f"⏳ {self._label(step)} — running…")
                else:
                    lines.append(f"⬜ {self._label(step)}")
        elif self.active:
            lines.append(f"⏳ {self._label(self.active)} — running…")
        lines.append(f"\n⏱️ Elapsed: {mins}m {secs}s · typical run ~5–10 min")
        await _discord_edit(self.ack, "\n".join(lines))


async def handle_discord_message(
    task: str,
    channel: Any,
    thread: Any | None,
    feedback_context: str | None,
    config: LabConfig,
    prompts: dict[str, str],
    client: Any | None = None,
    interaction: Any | None = None,
) -> None:
    """Route a Discord message to run_pipeline() and post the memo as a thread."""
    import discord

    global _last_task, _last_ticker

    display_task = task if len(task) <= 80 else task[:77] + "..."
    use_interaction = interaction is not None and thread is None
    try:
        ack = await _discord_post(
            f"🔬 Starting research on `{display_task}`…",
            interaction=interaction if use_interaction else None,
            channel=channel if not use_interaction else None,
            thread=thread if not use_interaction else None,
        )
    except discord.Forbidden as exc:
        raise LabConfigError(_channel_permission_hint(getattr(channel, "id", 0))) from exc

    progress = DiscordProgressBoard(ack, display_task)

    async def on_progress(step: str, event: str, meta: dict[str, Any]) -> None:
        if event == "queue":
            progress.set_queue(meta.get("steps") or [])
        elif event == "start":
            await progress.mark_start(step)
        elif event == "done":
            await progress.mark_done(step, **meta)

    pipeline_task = task
    if feedback_context:
        pipeline_task = f"[Feedback]\n{feedback_context}\n\n{task}"

    try:
        result = await run_pipeline(
            pipeline_task, config, prompts, on_progress=on_progress
        )
    except Exception as exc:
        await _discord_edit(ack, f"❌ Pipeline failed: {exc}")
        return

    if result.get("status") == "failed" or not result.get("final_memo"):
        steps = result.get("steps") or {}
        step_summary = {
            key: (len(val) if isinstance(val, str) else type(val).__name__)
            for key, val in steps.items()
        }
        failed = result.get("status", "failed")
        await _discord_edit(
            ack,
            (
                f"❌ Pipeline error at step `{failed}` — no memo produced.\n"
                f"```json\n{json.dumps(step_summary, indent=2)[:1500]}\n```"
            ),
        )
        return

    _last_task = pipeline_task
    plan = result.get("dispatch_plan") or {}
    if plan.get("ticker"):
        _last_ticker = str(plan["ticker"])

    memo_text = _strip_memo_fences(str(result["final_memo"]))
    fields = _extract_memo_fields(memo_text, result)
    parent_text = extract_memo_header(memo_text)
    if not parent_text.startswith("INVESTMENT MEMO"):
        parent_text = (
            f"INVESTMENT MEMO — {fields['ticker']} {fields['region']}\n"
            f"Stamped: {fields['stamp']}\n"
            f"─────────────────────────────────\n"
            f"Conviction: {fields['conviction']} | PT: {fields['price_target']} | "
            f"Upside: {fields['upside']}\n"
            f"Divergence: {fields['divergence']}"
        )

    parent_msg = await _discord_post(
        parent_text,
        interaction=interaction if use_interaction else None,
        channel=channel if not use_interaction else None,
        thread=thread if not use_interaction else None,
    )
    thread_name = f"{fields['ticker']} — {date.today().isoformat()}"
    memo_thread = await _create_memo_thread(
        channel,
        parent_msg,
        thread_name,
        use_interaction=use_interaction,
    )

    sections = parse_memo_sections(memo_text)
    title_by_num = dict(MEMO_SECTION_TITLES)

    async def _post_section(text: str) -> None:
        if memo_thread is not None:
            try:
                for chunk in format_discord_message(text):
                    await memo_thread.send(chunk)
                return
            except discord.Forbidden:
                pass
        for chunk in format_discord_message(text):
            await _discord_post(
                chunk,
                interaction=interaction if use_interaction else None,
                channel=channel if not use_interaction else None,
                thread=thread if not use_interaction else None,
                wait=False,
            )

    if "full" in sections:
        await _post_section(sections["full"])
    else:
        for num, body in sections.items():
            title = title_by_num.get(num, f"Section {num}")
            section_text = f"**{num}. {title}**\n\n{body.strip()}"
            await _post_section(section_text)

    footer = (
        "---\n"
        "Use `/rerun`, `/rerun-all`, `/lock <section>`, or `/macro <feedback>` to interact.\n"
    )
    if memo_thread is not None:
        footer += "Reply in this thread to provide feedback for a re-run."
    else:
        footer += (
            "Thread creation failed — grant **Create Public Threads**. "
            "Use slash commands for feedback until View Channel is fixed."
        )
    await _post_section(footer)
    await _discord_edit(
        ack,
        f"✅ **Pipeline complete** — `{display_task}`\nMemo posted below.",
    )


def _validate_discord_env() -> tuple[str, str, str]:
    load_dotenv(ROOT / ".env")
    load_dotenv()

    def _clean(val: str) -> str:
        return val.strip().strip('"').strip("'")

    token = _clean(os.getenv("DISCORD_BOT_TOKEN", ""))
    guild_id = _clean(os.getenv("DISCORD_GUILD_ID", ""))
    channel_id = _clean(os.getenv("DISCORD_CHANNEL_ID", ""))
    missing = [
        name
        for name, val in (
            ("DISCORD_BOT_TOKEN", token),
            ("DISCORD_GUILD_ID", guild_id),
            ("DISCORD_CHANNEL_ID", channel_id),
        )
        if not val
    ]
    if missing:
        raise LabConfigError(
            "Discord mode requires: "
            + ", ".join(missing)
            + ". Local CLI works without Discord."
        )
    for label, val in (
        ("DISCORD_GUILD_ID", guild_id),
        ("DISCORD_CHANNEL_ID", channel_id),
    ):
        if not val.isdigit():
            raise LabConfigError(
                f"{label} must be a numeric snowflake ID (got {val!r}). "
                "Enable Developer Mode and copy the ID again."
            )
    return token, guild_id, channel_id


def run_discord_mode() -> int:
    """Discord Gateway bridge per SPEC §7."""
    import discord
    from discord import app_commands

    try:
        token, guild_id_str, channel_id_str = _validate_discord_env()
    except LabConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        config = load_config()
        prompts = load_prompt_registry()
    except LabConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    configure_provider_environment(config)

    guild_id = int(guild_id_str)
    channel_id = int(channel_id_str)
    guild_obj = discord.Object(id=guild_id)

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    async def _channel_for_interaction(interaction: discord.Interaction) -> Any | None:
        return await _resolve_slash_channel(
            client, guild_id, channel_id, interaction
        )

    async def _send_interaction_notice(
        interaction: discord.Interaction, text: str
    ) -> None:
        try:
            await interaction.followup.send(text[:1990], ephemeral=True)
        except discord.HTTPException:
            pass

    async def _run_pipeline_cmd(
        interaction: discord.Interaction,
        task: str,
        feedback_context: str | None,
        started_msg: str,
    ) -> None:
        ch = await _channel_for_interaction(interaction)
        if ch is None:
            hint = await _channel_access_hint(
                client, guild_id, channel_id, interaction=interaction
            )
            await _send_interaction_notice(interaction, hint)
            return
        await interaction.followup.send(started_msg, ephemeral=True)
        try:
            await handle_discord_message(
                task,
                ch,
                None,
                feedback_context,
                config,
                prompts,
                client=client,
                interaction=interaction,
            )
        except LabConfigError as exc:
            await _send_interaction_notice(interaction, f"❌ {exc}")
        except discord.Forbidden:
            await _send_interaction_notice(
                interaction, f"❌ {_missing_access_setup_hint()}"
            )
        except Exception as exc:
            await _send_interaction_notice(
                interaction, f"❌ Pipeline posted with errors: {exc}"
            )

    @client.event
    async def on_ready() -> None:
        guild = client.get_guild(guild_id)
        if guild is None:
            msg = (
                f"Bot is not in server {guild_id_str} (DISCORD_GUILD_ID). "
                "Re-invite with scopes bot + applications.commands, or fix the guild ID."
            )
            print(json.dumps({"status": "discord_bridge_error", "error": msg}), file=sys.stderr)
            await client.close()
            return

        ch = client.get_channel(channel_id)
        if ch is None:
            print(
                json.dumps(
                    {
                        "status": "discord_bridge_warning",
                        "warning": (
                            f"Channel {channel_id_str} not visible. "
                            "Check DISCORD_CHANNEL_ID and bot channel permissions."
                        ),
                    }
                ),
                file=sys.stderr,
            )
        elif isinstance(ch, discord.TextChannel):
            me = await _ensure_bot_member(guild, client)
            if me is not None:
                perms = ch.permissions_for(me)
                print(
                    json.dumps(
                        {
                            "status": "discord_channel_permissions",
                            "channel_id": channel_id_str,
                            "can_send": _member_can_post(perms),
                            "can_create_threads": _perms_can_create_threads(perms),
                            "view_channel": bool(perms.view_channel),
                            "send_messages": bool(perms.send_messages),
                            "create_public_threads": bool(
                                perms.create_public_threads
                            ),
                        }
                    ),
                    flush=True,
                )

        try:
            synced = await tree.sync(guild=guild_obj)
        except discord.Forbidden:
            msg = (
                "403 Missing Access (50001) syncing slash commands. "
                "Re-invite the bot using BOTH scopes: bot AND applications.commands. "
                "Developer Portal → OAuth2 → URL Generator → select those scopes → "
                "use the generated invite link. Guild ID must be the server ID, not the channel ID."
            )
            print(json.dumps({"status": "discord_bridge_error", "error": msg}), file=sys.stderr)
            await client.close()
            return
        except discord.HTTPException as exc:
            print(
                json.dumps({"status": "discord_bridge_error", "error": str(exc)}),
                file=sys.stderr,
            )
            await client.close()
            return

        print(
            json.dumps(
                {
                    "status": "discord_bridge_running",
                    "bot": str(client.user),
                    "guild": guild.name,
                    "guild_id": guild_id_str,
                    "channel_id": channel_id_str,
                    "commands_synced": len(synced),
                }
            ),
            flush=True,
        )

    @client.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot:
            return
        if not isinstance(message.channel, discord.Thread):
            return
        if message.channel.parent_id != channel_id:
            return
        parent_channel = message.channel.parent
        if parent_channel is None:
            return
        await handle_discord_message(
            task=f"Re-run with feedback: {message.content}",
            channel=parent_channel,
            thread=message.channel,
            feedback_context=message.content,
            config=config,
            prompts=prompts,
            client=client,
        )

    @tree.command(
        name="lab-check",
        description="Show bot channel permissions and configured channel IDs",
        guild=guild_obj,
    )
    async def lab_check_cmd(interaction: discord.Interaction) -> None:
        report = await _channel_permission_report(
            client, guild_id, channel_id, interaction=interaction
        )
        await interaction.response.send_message(report[:1990], ephemeral=True)

    @tree.command(
        name="research",
        description="Run the research pipeline on a ticker or task",
        guild=guild_obj,
    )
    async def research_cmd(interaction: discord.Interaction, task: str) -> None:
        await interaction.response.defer(ephemeral=True)
        await _run_pipeline_cmd(
            interaction,
            task,
            None,
            f"Pipeline started in <#{interaction.channel.id}>.",
        )

    @tree.command(
        name="rerun",
        description="Re-run the last pipeline (full memo; section arg ignored in v0.1)",
        guild=guild_obj,
    )
    async def rerun_cmd(interaction: discord.Interaction, section: str = "all") -> None:
        await interaction.response.defer(ephemeral=True)
        if not _last_task:
            await interaction.followup.send(
                "No previous task found. Use `/research <task>` first."
            )
            return
        await _run_pipeline_cmd(
            interaction,
            _last_task,
            f"Re-run requested for section: {section}",
            "Re-run started.",
        )

    @tree.command(
        name="rerun-all",
        description="Re-run the full pipeline for the last task",
        guild=guild_obj,
    )
    async def rerun_all_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not _last_task:
            await interaction.followup.send(
                "No previous task found. Use `/research <task>` first."
            )
            return
        await _run_pipeline_cmd(interaction, _last_task, None, "Full re-run started.")

    @tree.command(
        name="lock",
        description="Lock a memo section to preserve it on re-run",
        guild=guild_obj,
    )
    async def lock_cmd(interaction: discord.Interaction, section: str) -> None:
        ticker = detect_ticker(_last_task or "")
        if not ticker:
            await interaction.response.send_message(
                "Could not detect ticker from last task. Run `/research` first."
            )
            return
        _write_locked_section(ticker, section)
        await interaction.response.send_message(
            f"✅ Section `{section}` locked for `{ticker}`. "
            "It will be preserved on re-run."
        )

    @tree.command(
        name="macro",
        description="Re-run macro analysis with feedback",
        guild=guild_obj,
    )
    async def macro_cmd(interaction: discord.Interaction, feedback: str) -> None:
        await interaction.response.defer(ephemeral=True)
        ticker = detect_ticker(_last_task or "")
        task = f"Re-run macro step with feedback: {feedback}"
        if ticker:
            task += f" for {ticker}"
        await _run_pipeline_cmd(interaction, task, feedback, "Macro re-run started.")

    try:
        client.run(token)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"Discord bridge error: {exc}", file=sys.stderr)
        return 1


def run_slack_mode() -> int:
    """Deprecated — Slack replaced by Discord."""
    print(
        "Slack bridge replaced by Discord in v0.1.\nUse: python lab.py --discord",
        file=sys.stderr,
    )
    return 1


def test_discord_helpers() -> int:
    """Smoke test Discord helpers without live tokens."""
    sample_memo = (
        "INVESTMENT MEMO — 9988.HK HK\n"
        "Stamped: lab.md v0.1.0 | coverage.md abc123 | 2026-05-28\n"
        "─────────────────────────────────\n"
        "Conviction: HOLD | PT: HK$120 | Upside: 5%\n"
        "1. Investment Thesis\nBullet one.\n"
        "2. Factor Regime\nRegime text.\n"
        "3. Fundamental Snapshot\nSnapshot text.\n"
        "4. Regional Context\nContext.\n"
        "5. Sell-Side Consensus\nConsensus.\n"
        "6. Scenario Analysis\nScenarios.\n"
        "7. Catalysts & Timeline\nCatalysts.\n"
        "8. Risks\nRisk one.\n"
    )
    sections = parse_memo_sections(sample_memo)
    header = extract_memo_header(sample_memo)
    long_text = "Line.\n" * 800
    chunks = format_discord_message(long_text, max_len=1900)

    test_ticker = "TEST.HK"
    test_dir = COVERAGE_STATE / test_ticker
    test_dir.mkdir(parents=True, exist_ok=True)
    _write_locked_section(test_ticker, "thesis")
    lock_path = test_dir / COVERAGE_STATE_FILES["locked_sections"]
    lock_ok = lock_path.is_file()

    slash_commands = ["research", "rerun", "rerun-all", "lock", "macro", "lab-check"]
    print(json.dumps({"parse_memo_sections": sections}, indent=2))
    print(json.dumps({"extract_memo_header": header}, indent=2))
    print(
        json.dumps(
            {
                "format_discord_message_chunks": len(chunks),
                "max_chunk_len": max(len(c) for c in chunks),
                "write_locked_section": lock_ok,
                "slash_commands": slash_commands,
                "status": "discord_wiring_ok",
            },
            indent=2,
        )
    )
    return 0


async def run_cli(task: str) -> int:
    """Local CLI entrypoint (no Discord required)."""
    try:
        config = load_config()
        prompts = load_prompt_registry()
    except LabConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    configure_provider_environment(config)

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
        "--discord",
        action="store_true",
        help="Start Discord bot bridge",
    )
    parser.add_argument(
        "--test-discord",
        action="store_true",
        help="Run Discord helper smoke test (no live tokens)",
    )
    parser.add_argument(
        "--slack",
        action="store_true",
        help="Deprecated — use --discord",
    )
    args = parser.parse_args()

    if args.test_discord:
        return test_discord_helpers()

    if args.slack:
        return run_slack_mode()

    if args.discord:
        return run_discord_mode()

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
