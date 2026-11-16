"""Check the OpenRouter key: what it has spent, what is left, and what the VLM model costs.

    python -m shared.model_adapters.check_openrouter                    # the model in VLM_MODEL
    python -m shared.model_adapters.check_openrouter --model qwen/qwen2.5-vl-72b-instruct:free
    python -m shared.model_adapters.check_openrouter --model a/one --model b/two
    python -m shared.model_adapters.check_openrouter --estimate 2500 300  # cost of one call
    python -m shared.model_adapters.check_openrouter --json             # the raw answers

The key is VLM_API_KEY, else OPENROUTER_API_KEY (from the environment or .env). Nothing is sent
to a model, so the check costs nothing. OpenRouter answers from:

- GET /key: the key's spending cap and what is left of it, its spend today, this week, this
  month and in total (USD), and the requests made today to free (":free") models.
- GET /models: every model's price (USD per token, per image, per request) and whether it
  accepts images.

The account's credit balance needs a management key, so it is not shown; a key without a cap
can spend until the balance runs out.
"""

import argparse
import json
import sys

import httpx

from shared.config import get_settings

OPENROUTER_URL = "https://openrouter.ai/api/v1"
# OpenRouter's documented limits for ":free" models (per account, the UTC day resets them).
FREE_PER_MINUTE = 20
FREE_PER_DAY_LOW, FREE_PER_DAY_HIGH, FREE_CREDITS_THRESHOLD = 50, 1000, 10


class CheckFailed(Exception):
    pass


def _get(client: httpx.Client, path: str) -> dict:
    try:
        response = client.get(path)
    except httpx.HTTPError as error:
        raise CheckFailed(f"could not reach OpenRouter: {error}") from error
    if response.status_code in (401, 403):
        raise CheckFailed(
            f"OpenRouter refused the key (HTTP {response.status_code}): {response.text[:200]}"
        )
    if response.status_code != 200:
        raise CheckFailed(f"GET {path} failed (HTTP {response.status_code}): {response.text[:200]}")
    try:
        return response.json()["data"]
    except (ValueError, KeyError, TypeError) as error:
        raise CheckFailed(f"GET {path}: unexpected answer: {response.text[:200]}") from error


def _usd(value) -> str:
    if value is None:
        return "-"
    return f"${value:,.4f}" if abs(value) < 1 else f"${value:,.2f}"


def _price(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def key_report(key: dict) -> list[str]:
    lines = ["OpenRouter key"]
    lines.append(f"  label:            {key.get('label') or '-'}")
    tier = "free tier (no credits bought)" if key.get("is_free_tier") else "paid (credits bought)"
    lines.append(f"  account:          {tier}")
    limit = key.get("limit")
    if limit is None:
        lines.append("  spending cap:     none on this key (it can use the whole account balance)")
    else:
        reset = key.get("limit_reset")
        lines.append(f"  spending cap:     {_usd(limit)}" + (f", resets {reset}" if reset else ""))
        remaining = key.get("limit_remaining")
        used = None if remaining is None else max(limit - remaining, 0)
        share = f" ({used / limit:.0%} used)" if used is not None and limit else ""
        lines.append(f"  left on the cap:  {_usd(remaining)}{share}")
    lines.append(
        "  spent:            "
        f"today {_usd(key.get('usage_daily'))}, this week {_usd(key.get('usage_weekly'))}, "
        f"this month {_usd(key.get('usage_monthly'))}, in total {_usd(key.get('usage'))}"
    )
    if key.get("byok_usage"):
        lines.append(f"  own provider keys (BYOK): {_usd(key.get('byok_usage'))} in total")
    if key.get("expires_at"):
        lines.append(f"  expires:          {key['expires_at']}")
    for budget in key.get("budgets") or []:
        if budget.get("scope") == "key_limit":  # the spending cap above
            continue
        lines.append(
            f"  budget ({budget.get('scope')}): {_usd(budget.get('spend_usd'))} of "
            f"{_usd(budget.get('limit_usd'))} spent, {_usd(budget.get('remaining_usd'))} left"
            + (f", resets {budget['reset_interval']}" if budget.get("reset_interval") else "")
        )

    lines.append("")
    lines.append("Free models (ids ending in ':free'), counted per account, reset at 00:00 UTC")
    free = key.get("free_model_daily_requests")
    if free:
        lines.append(
            f"  requests today:   {free.get('used')} used of {free.get('limit')}, "
            f"{free.get('remaining')} left"
        )
    else:
        lines.append(
            f"  requests today:   not reported; the limit is {FREE_PER_DAY_LOW} a day before "
            f"{FREE_CREDITS_THRESHOLD} credits are bought, {FREE_PER_DAY_HIGH} after"
        )
    lines.append(f"  per minute:       at most {FREE_PER_MINUTE} (not reported by the API)")
    return lines


def model_report(model_id: str, models: dict[str, dict], key: dict, estimate) -> list[str]:
    model = models.get(model_id)
    if model is None:
        return [
            f"Model {model_id}",
            "  not found on OpenRouter (check the id at https://openrouter.ai/models)",
        ]
    pricing = model.get("pricing") or {}
    prompt, completion = _price(pricing.get("prompt")), _price(pricing.get("completion"))
    image, request = _price(pricing.get("image")), _price(pricing.get("request"))
    modalities = (model.get("architecture") or {}).get("input_modalities") or []
    free = model_id.endswith(":free") or not any((prompt, completion, image, request))

    lines = [f"Model {model_id}" + (f" ({model['name']})" if model.get("name") else "")]
    lines.append(
        f"  accepts images:   {'yes' if 'image' in modalities else 'NO, it cannot be the VLM'}"
    )
    if model.get("context_length"):
        lines.append(f"  context:          {model['context_length']:,} tokens")
    if free:
        lines.append("  price:            free (uses the free-model daily requests above)")
        free_left = (key.get("free_model_daily_requests") or {}).get("remaining")
        if free_left is not None:
            lines.append(f"  calls left today: {free_left}")
        return lines

    lines.append(
        f"  price:            {_usd(prompt * 1e6)} per 1M input tokens, "
        f"{_usd(completion * 1e6)} per 1M output tokens"
    )
    extras = []
    if image:
        extras.append(f"{_usd(image)} per image")
    if request:
        extras.append(f"{_usd(request)} per request")
    if extras:
        lines.append(f"                    {', '.join(extras)}")
    if estimate:
        tokens_in, tokens_out = estimate
        per_call = tokens_in * prompt + tokens_out * completion + request
        lines.append(
            f"  one call:         about {_usd(per_call)} ({tokens_in} in + {tokens_out} out tokens)"
        )
        remaining = key.get("limit_remaining")
        if remaining is not None and per_call > 0:
            lines.append(
                f"  calls left:       about {int(remaining / per_call):,} on the key's cap "
                "(fewer if the account balance runs out first)"
            )
    return lines


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--model",
        action="append",
        help="OpenRouter model id to price, e.g. qwen/qwen3-vl-30b-a3b-instruct; repeat for "
        "several (default: VLM_MODEL)",
    )
    parser.add_argument(
        "--estimate",
        nargs=2,
        type=int,
        metavar=("IN", "OUT"),
        help="estimate the cost of one call with IN input and OUT output tokens "
        "(check_vlm prints a real call's counts)",
    )
    parser.add_argument("--json", action="store_true", help="print OpenRouter's raw answers")
    args = parser.parse_args(argv)

    settings = get_settings()
    api_key = settings.vlm_key
    if not api_key:
        print("no key: set OPENROUTER_API_KEY (or VLM_API_KEY) in .env", file=sys.stderr)
        return 2
    base_url = settings.vlm_base_url.rstrip("/")
    if "openrouter.ai" not in base_url:
        print(
            f"note: VLM_BASE_URL is {base_url}, not OpenRouter; checking the key at {OPENROUTER_URL}"
        )
        base_url = OPENROUTER_URL
    model_ids = args.model or [settings.vlm_model]

    headers = {"Authorization": f"Bearer {api_key}", "X-Title": "Sentinel"}
    with httpx.Client(
        base_url=base_url, headers=headers, timeout=30, transport=transport
    ) as client:
        try:
            key = _get(client, "/key")
            models = {
                m["id"]: m for m in _get(client, "/models") if isinstance(m, dict) and "id" in m
            }
        except CheckFailed as error:
            print(f"failed: {error}", file=sys.stderr)
            return 1

    if args.json:
        print(json.dumps({"key": key, "models": {m: models.get(m) for m in model_ids}}, indent=2))
        return 0 if all(m in models for m in model_ids) else 1

    lines = key_report(key)
    for model_id in model_ids:
        lines += [""] + model_report(model_id, models, key, args.estimate)
    print("\n".join(lines))
    return 0 if all(m in models for m in model_ids) else 1


if __name__ == "__main__":
    sys.exit(main())
