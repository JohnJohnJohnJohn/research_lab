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
from dataclasses import dataclass, field
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


def build_agents(
    prompts: dict[str, str],
    config: LabConfig,
    mcp_server: Any | None,
    coverage_context: str = "",
) -> dict[str, Any]:
    """Instantiate all nine agents; attach MCP to data-consuming roles."""
    from agents import Agent

    mcp_roles = set(CAPABILITIES["standard_market_data"]["assigned_roles"])
    mcp_list = [mcp_server] if mcp_server is not None else []

    sub_agents: dict[str, Agent] = {}
    for role in PROMPT_PATHS:
        if role == "director":
            continue
        sub_agents[role] = Agent(
            name=role,
            instructions=prompts[role],
            model=config.resolved_models[role],
            mcp_servers=mcp_list if role in mcp_roles else [],
        )

    director = Agent(
        name="director",
        instructions=build_director_instructions(prompts, coverage_context),
        model=config.resolved_models["director"],
        handoffs=list(sub_agents.values()),
    )

    agents = {"director": director, **sub_agents}
    return agents


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


async def run_director_task(task: str, config: LabConfig, prompts: dict[str, str]) -> dict[str, Any]:
    """Route a user task through the Director via OpenAI Agents SDK."""
    from agents import Runner
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

    eodhd = build_eodhd_stdio_server(config)
    async with eodhd:
        agents = build_agents(prompts, config, eodhd, coverage_context)
        director = agents["director"]

        max_tokens = resolve_max_output_tokens()
        run_config = RunConfig(
            model_provider=LitellmProvider(),
            model_settings=ModelSettings(max_tokens=max_tokens),
        )
        result = await Runner.run(
            director,
            task,
            run_config=run_config,
            max_turns=10,
        )

    return {
        "boot": report.to_dict(),
        "task": task,
        "director_response": result.final_output,
        "status": "completed",
    }


async def run_cli(task: str) -> int:
    """Local non-Slack entrypoint."""
    try:
        config = load_config()
        prompts = load_prompt_registry()
    except LabConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    try:
        output = await run_director_task(task, config, prompts)
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

    # TODO(SPEC §7): Socket Mode listener → handle_slack_message(event) → run_director_task
    print(
        "Slack env present. Full Slack bridge not implemented in v0.1 boot. "
        "Use: python lab.py \"<task>\" for local Director runs.",
    )
    return 0


def handle_slack_message(event: dict[str, Any]) -> None:
    """Placeholder interface for SPEC §7 Slack → Director routing."""
    # TODO: parse event text, call asyncio.run(run_director_task(...)), post threaded memo
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
