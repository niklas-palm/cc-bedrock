"""Anthropic models on Bedrock: one model whichever region prefix or context window an ID asks for, its name for
people, and the choices Claude Code's /model picker offers."""

import re

FAMILIES = ("opus", "sonnet", "haiku", "fable")
# Where a cross-region inference profile sends requests: its ID's prefix
ROUTES = {"eu": "EU", "us": "US", "apac": "Asia Pacific", "jp": "Japan", "au": "Australia", "ca": "Canada",
          "global": "Global (any commercial region)"}


def route(model_id: str) -> str:
    """eu.anthropic.claude-opus-5-5 -> eu"""
    return model_id.split(".")[0]


def base_model(model_id: str) -> str:
    """eu.anthropic.claude-opus-4-6-v1[1m] -> anthropic.claude-opus-4-6-v1: one model, whichever regions it routes
    to, and whatever context window Claude Code asks for."""
    model_id = model_id.removesuffix("[1m]")
    return model_id[model_id.index("anthropic."):] if "anthropic." in model_id else model_id


def model_name(model_id: str) -> str:
    """eu.anthropic.claude-opus-5-5, global.anthropic.claude-opus-5-5[1m] and claude-opus-5-5 are all `Opus 5.5`."""
    if not model_id:
        return ""
    name = re.sub(r"^(?:[a-z]+\.)?anthropic\.", "", model_id.split("/")[-1])
    name = re.sub(r"-v\d+(:\d+)?$|-\d{8}(-v\d+:\d+)?$|\[1m\]$", "", name).removeprefix("claude-")
    match = re.fullmatch(r"([a-z]+)-(\d+)(?:-(\d+))?", name)
    if match:
        family, major, minor = match.groups()
        return f"{family.title()} {major}.{minor}" if minor else f"{family.title()} {major}"
    return name


def available(bedrock) -> dict:
    """{model: [its inference profile IDs]} for the Anthropic models Bedrock offers in this region."""
    models = {}
    for page in bedrock.get_paginator("list_inference_profiles").paginate(typeEquals="SYSTEM_DEFINED"):
        for profile in page["inferenceProfileSummaries"]:
            model = base_model(profile["inferenceProfileId"])
            if model.startswith("anthropic."):
                models.setdefault(model, []).append(profile["inferenceProfileId"])
    return models


def order(model: str):
    """Opus, Sonnet, Haiku, Fable, and the newest first within each."""
    name = model_name(model).lower()
    family = name.split(" ")[0]
    major, minor = ([int(part) for part in re.findall(r"\d+", name)] + [0, 0])[:2]  # Opus 5 is 5.0, below 5.5
    return (FAMILIES.index(family) if family in FAMILIES else len(FAMILIES), -major, -minor, name)


def choices(models: dict, disabled: list, default: str, configured: dict, routes_off: list = ()) -> dict:
    """What Claude Code offers people: a /model row per allowed model it can route, and the model new sessions
    start on.

    `configured` is config.yaml's {tier: model ID}. Those IDs are used as they are (their region prefix, a [1m]),
    and their prefix is preferred for the other models, so everything routes the same way. A model whose every
    profile goes through a routing that's off (only global., with Global off) isn't offered."""
    by_model = {base_model(model_id): model_id for model_id in configured.values()}
    prefix = route(next(iter(configured.values()), ""))
    rows = []
    for model in sorted((m for m in models if m not in disabled), key=order):
        profiles = [p for p in models[model] if route(p) not in routes_off]
        if not profiles:
            continue
        model_id = by_model.get(model) or next((p for p in profiles if route(p) == prefix), None) or \
            next((p for p in profiles if route(p) != "global"), profiles[0])
        rows.append({"model": model_id, "label": model_name(model)})
    start = next((row["model"] for row in rows if base_model(row["model"]) == default), None)
    return {"options": rows, "default": start}
