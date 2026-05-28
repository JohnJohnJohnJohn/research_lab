#!/usr/bin/env python3
"""Research Lab boot script v0.1.

Loads doctrine and prompts, resolves per-role models, instantiates agents,
registers MCP capabilities, and routes tasks to the Director entrypoint.

Local CLI (no Slack required):
    python lab.py "Initiate coverage on 9988 HK"

Slack seam (stub):
    python lab.py --slack
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
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
    return ticker.upper() in coverage_md.upper()


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
) -> str:
    return (
        prompts["lab_md"][:2000]
        + "\n\n---\n\n# Active context (coverage.md)\n\n"
        + prompts["coverage_md"]
        + (f"\n\n{coverage_context}" if coverage_context else "")
    )


def build_synthesize_instructions(prompts: dict[str, str]) -> str:
    director_synthesis = extract_md_section(prompts["director"], "4. Synthesis")
    quality_gates = extract_md_section(prompts["lab_md"], "5. Quality Gates")
    if not director_synthesis:
        director_synthesis = prompts["director"]
    return director_synthesis + "\n\n---\n\n" + quality_gates


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
    coverage_context = ""
    if ticker:
        state = load_coverage_state(ticker)
        coverage_context = build_coverage_context(ticker, state)

    steps: dict[str, Any] = {}
    specialist_outputs: dict[str, str] = {}
    pipeline_status = "completed"

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
            "Do not perform analysis. Do not produce a memo. Return JSON only — no prose."
        )
        classify_out, classify_turns, classify_status = await _run_agent_step(
            "director",
            build_classify_instructions(prompts, coverage_context),
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

        # Step 4 — Regional analyst(s), sequential
        analyst_parts: list[str] = []
        regional_roles = REGIONAL_ROLES.get(plan.region, "hk_analyst")
        if isinstance(regional_roles, str):
            regional_roles = [regional_roles]

        for analyst_role in regional_roles:
            if analyst_role not in plan.agents_needed:
                continue
            analyst_msg = (
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


def run_slack_mode() -> int:
    """Thin Slack seam per SPEC §7 — full bridge deferred."""
    load_dotenv(ROOT / ".env")
    load_dotenv()

    missing = [
        name
        for name, val in (
            ("SLACK_BOT_TOKEN", os.getenv("SLACK_BOT_TOKEN", "").strip()),
            ("SLACK_APP_TOKEN", os.getenv("SLACK_APP_TOKEN", "").strip()),
            ("SLACK_CHANNEL_ID", os.getenv("SLACK_CHANNEL_ID", "").strip()),
        )
        if not val
    ]
    if missing:
        print(
            "Slack mode requires: "
            + ", ".join(missing)
            + ". Local CLI mode works without Slack.",
            file=sys.stderr,
        )
        return 1

    # TODO(SPEC §7): Socket Mode listener → handle_slack_message(event) → run_pipeline
    print(
        "Slack env present. Full Slack bridge not implemented in v0.1 boot. "
        "Use: python lab.py \"<task>\" for local Director runs.",
    )
    return 0


def handle_slack_message(event: dict[str, Any]) -> None:
    """Placeholder interface for SPEC §7 Slack → Director routing."""
    # TODO: parse event text, call asyncio.run(run_pipeline(...)), post threaded memo
    raise NotImplementedError("Slack message handling is not implemented in lab.py v0.1")


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
        help="Enter Slack-compatible mode (stub in v0.1)",
    )
    args = parser.parse_args()

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
