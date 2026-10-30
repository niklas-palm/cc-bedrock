"""Anthropic models on Bedrock: one model whichever region prefix or context window an ID asks for, its name for
people, and the choices Claude Code's /model picker offers."""

import re

FAMILIES = ("opus", "sonnet", "haiku", "fable")
MODEL_ID = re.compile(r"anthropic\.[a-z0-9.:-]+")  # anthropic.claude-opus-5-5: what admins turn on and off (fullmatch)
ROUTE_ID = re.compile(r"[a-z]{2,8}")  # eu, global
# Where a cross-region inference profile sends requests: its ID's prefix
ROUTES = {"eu": "EU", "us": "US", "apac": "Asia Pacific", "jp": "Japan", "au": "Australia", "ca": "Canada",
          "global": "Global"}


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
    name = re.sub(r"^(?:[a-z]+\.)?anthropic\.", "", model_id.split("/")[-1].removesuffix("[1m]"))
    name = re.sub(r"-v\d+(:\d+)?$|-\d{8}(-v\d+:\d+)?$", "", name).removeprefix("claude-")
    match = re.fullmatch(r"([a-z]+)-(\d+)(?:-(\d+))?", name)
    if match:
        family, major, minor = match.groups()
        return f"{family.title()} {major}.{minor}" if minor else f"{family.title()} {major}"
    return name


def from_config(configured: dict, bedrock) -> dict:
    """config.yaml's models ({tier: ID}) as {model: ID}. An application inference profile's ARN is the model behind
    it."""
    result = {}
    for model_id in configured.values():
        model = base_model(model_id)
        if model_id.startswith("arn:") and "application-inference-profile/" in model_id:
            arn = bedrock.get_inference_profile(inferenceProfileIdentifier=model_id)["models"][0]["modelArn"]
            model = arn.split("foundation-model/")[-1]
        result[model] = model_id
    return result


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


def tiers(configured: dict, options: list) -> dict:
    """Claude Code's opus, sonnet and haiku models ({tier: ID}): config.yaml's while the admins allow it, else the
    same model through a routing that's on, else the newest allowed one of the nearest family (for haiku: Haiku,
    then Sonnet, then Opus). So nothing Claude Code reaches for on its own (a session's start, a subagent, its small
    background calls) is a model Bedrock refuses, and those small calls don't end up on Opus."""
    offered = [option["model"] for option in options]
    nearest_first = {"opus": ["opus", "sonnet", "haiku"], "sonnet": ["sonnet", "opus", "haiku"],
                     "haiku": ["haiku", "sonnet", "opus"]}

    def rank(model, tier):
        family = model_name(model).lower().split(" ")[0]
        return nearest_first[tier].index(family) if family in nearest_first[tier] else len(nearest_first[tier])

    result = {}
    for tier, model_id in configured.items():
        same_model = [m for m in offered if base_model(m) == base_model(model_id)]
        nearest = sorted(offered, key=lambda m: rank(m, tier))  # sorted() keeps newest first within a family
        result[tier] = model_id if model_id in offered or not offered else (same_model + nearest)[0]
    return result


def choices(models: dict, settings: dict, config: dict) -> dict:
    """What Claude Code offers people: a /model row per allowed model it can route, and the model new sessions
    start on. `settings` is the admins' (config/models.json), `config` is from_config()'s.

    config.yaml's IDs are used as they are (their region prefix, a [1m], an application profile's ARN) while their
    routing is allowed, and their prefix is preferred for the other models, so everything routes the same way. A
    model whose every profile goes through a routing that's off (only global., with Global off) isn't offered."""
    disabled, routes_off, default = settings.get("disabled") or [], settings.get("routes_off") or [], \
        settings.get("default") or ""
    by_model = {model: model_id for model, model_id in config.items()
                if model_id.startswith("arn:") or route(model_id) not in routes_off}
    prefix = next((route(model_id) for model_id in by_model.values() if not model_id.startswith("arn:")), "")
    rows, start = [], None
    for model in sorted((m for m in {**models, **dict.fromkeys(by_model)} if m not in disabled), key=order):
        profiles = [p for p in models.get(model, []) if route(p) not in routes_off]
        if not profiles and model not in by_model:
            continue
        model_id = by_model.get(model) or next((p for p in profiles if route(p) == prefix), None) or \
            next((p for p in profiles if route(p) != "global"), profiles[0])
        rows.append({"model": model_id, "label": model_name(model)})
        if model == default:
            start = model_id
    return {"options": rows, "default": start}
