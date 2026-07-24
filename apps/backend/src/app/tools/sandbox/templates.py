"""CC PBX-style starter template for editable sandboxed Python tools.

No secrets — bind a tenant credential (JSON body tokens) and set base URLs in settings.
"""

CC_PBX_STARTER_SOURCE = '''\
"""Contact-center PBX style toolkit (sandbox starter).

Configure settings.base_url to your HTTPS API host (must be allowlisted).
Attach a tenant credential as JSON with pbx_token_id, ccpl_token_id, and
ccpl_unique_token — tokens are sent in POST bodies, not Authorization headers.
"""

from __future__ import annotations

from typing import Any

_JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}


def _resolve_config(ctx) -> dict[str, Any]:
    """Single source of truth for API hosts, roots, tokens, and timeout."""
    s = ctx.settings
    return {
        "base_url": str(s.get("base_url", "")).rstrip("/"),
        "pbx_api_root": str(s.get("pbx_api_root", "hodupbx_api/v1.4/api")).strip("/"),
        "ccpl_api_root": str(s.get("ccpl_api_root", "ccpl_api/v1.4/api")).strip("/"),
        "pbx_token_id": str(s.get("pbx_token_id") or ""),
        "ccpl_token_id": str(s.get("ccpl_token_id") or ""),
        "ccpl_unique_token": str(s.get("ccpl_unique_token") or ""),
        "timeout": int(s.get("timeout") or 60),
    }


def _pbx_url(ctx, path: str) -> str:
    cfg = _resolve_config(ctx)
    return f"{cfg['base_url']}/{cfg['pbx_api_root']}/{path.lstrip('/')}"


def _ccpl_url(ctx, path: str) -> str:
    cfg = _resolve_config(ctx)
    return f"{cfg['base_url']}/{cfg['ccpl_api_root']}/{path.lstrip('/')}"


async def _post(ctx, url: str, payload: dict[str, Any], label: str) -> Any:
    """POST helper — returns parsed JSON body or raises RuntimeError."""
    body = await ctx.http.post(url, json=payload, headers=_JSON_HEADERS)
    if isinstance(body, dict) and str(body.get("status", "")).lower() == "error":
        raise RuntimeError(f"{label} failed: {body}")
    if isinstance(body, str) and body.startswith("Error"):
        raise RuntimeError(body)
    return body


async def create_tenant(
    ctx,
    tenant_name: str,
    tenant_username: str,
    tenant_password: str,
    tenant_email: str,
    first_name: str = "",
    last_name: str = "",
    business_name: str = "",
    phone_number: str = "",
    address1: str = "",
    city: str = "",
    state: str = "",
    zip: str = "",
    tenant_billplan_id: str = "2",
    rule_group_id: str = "115",
    tenant_currency: str = "64",
    tenant_country: str = "99",
    timezone_id: str = "472",
    tenant_bill_type: str = "POSTPAID",
    overrides: dict[str, Any] | None = None,
) -> Any:
    """Provision a new PBX tenant (customer account)."""
    cfg = _resolve_config(ctx)
    if not cfg["pbx_token_id"]:
        raise RuntimeError("pbx_token_id is required in tool settings/credential")
    payload: dict[str, Any] = {
        "token_id": cfg["pbx_token_id"],
        "tenant_name": tenant_name,
        "tenant_username": tenant_username,
        "tenant_password": tenant_password,
        "tenant_email": tenant_email,
        "tenant_language": ["en"],
        "tenant_default_language": "en",
        "tenant_moh_file": "",
        "tenant_currency": tenant_currency,
        "tenant_country": tenant_country,
        "timezone_id": timezone_id,
        "tenant_billplan_id": tenant_billplan_id,
        "rule_group_id": rule_group_id,
        "tenant_tax": "12",
        "tenant_bill_type": tenant_bill_type,
        "first_name": first_name,
        "last_name": last_name,
        "business_name": business_name,
        "address1": address1,
        "city": city,
        "state": state,
        "zip": zip,
        "phone_number": phone_number,
        "tenant_serv_id_ext": "1",
        "tenant_serv_id_ext_count": "7",
        "tenant_serv_id_ext_rate": "0.0000",
        "moh_customization": "ON",
        "tenant_ext_callerid_customization": "DID",
        "tenant_codec_pref": "Platform",
        "tenant_feature_password": "1754",
        "external_call_ring_type": "",
        "tenant_concurrent_calls": "0",
        "tenant_audio_codec": "OPUS,PCMA,PCMU,G729",
        "tenant_video_codec": "H263,H263+,H263++,H264,VP8",
        "tenant_reg_auth": "ON",
        "auto_call_record": "OFF",
        "ProfileCustomization": "ON",
        "tenant_auto_assign_service": "OFF",
        "user_role_access": "ON",
        "api_access": "ON",
        "tenant_payment": "ON",
        "PaymentBlocked": "OFF",
        "tenant_email_invoice": "ON",
        "tenant_due_alert": "ON",
        "tenant_auto_inactive": "OFF",
        "tenant_low_balance_alert": "ON",
        "tenant_due_notification": "ON",
        "ReviewInvoice": "ON",
        "tenant_disable_ext_call": "OFF",
        "auto_adjust_from_balance": "OFF",
        "universal_forward": "ON",
        "unavailable_forward": "ON",
        "shift_forward": "ON",
        "follow_me": "ON",
        "do_not_disturb": "ON",
        "call_recording": "ON",
        "bargein": "ON",
        "webphone": "ON",
        "busy_forward": "ON",
        "time_based_forward": "ON",
        "whitelist": "ON",
        "blacklist": "ON",
        "caller_id_block": "ON",
        "no_answer_forward": "ON",
        "selective_forward": "ON",
        "accept_blocked_caller_id": "ON",
        "call_return": "ON",
        "weekoff": "ON",
        "park": "ON",
        "call_based_callid_restriction": "ON",
        "holiday": "ON",
        "transfer": "ON",
        "call_screening": "ON",
        "redial": "ON",
        "BillPlan_DuePeriod": "1",
        "BillPlan_AlertPeriod": "0",
        "tenant_serv_id_simultaneous_calls": "29",
        "tenant_serv_id_simultaneous_calls_count": "10",
        "tenant_serv_id_simultaneous_calls_rate": "10",
        "call_threshold_plan": 0,
        "auto_assign_roaming_plan": "OFF",
        "allow_request_bill_plan": "OFF",
        "tenant_device_wallpaper_customization": "ON",
        "tenant_device_ringtone_customization": "ON",
        "allow_c2c_plugin": "OFF",
        "circle_id": "5",
        "tenant_cluster_id": "1",
        "advance_payment_adjust": "Adjust full Invoice",
        "mobility_type": "LIMITED",
        "bill_day": "8",
        "tenant_serv_id_did": "3",
        "tenant_serv_id_did_count": "1",
        "tenant_serv_id_did_rate": "70.0000",
        "tenant_outbound": "ON",
        "rp_id": "22",
        "call_alert": "OFF",
        "call_balance": "200.00",
        "alert_balance": "200.00",
        "prepand_digit": "",
        "allow_default_rule_group": "ON",
        "allow_zoho_access": "OFF",
        "allow_geo_service": "OFF",
        "map_myindia_charge": "0.0000",
        "google_api_charge": "0.0000",
        "tenant_charge": "100.0000",
        "did_fixed_line_numbers_id": "29",
        "did_fixed_line_numbers_count": "1",
        "did_fixed_line_numbers_rate": "0.0000",
        "max_rec_storage_days": "30",
        "rec_storage": "OFF",
        "default_date_format": "YYYY-MM-DD HH:MM:SS",
        "invoice_date_format": "YYYY-MM-DD HH:MM:SS",
        "reseller": "",
    }
    if overrides and isinstance(overrides, dict):
        payload.update(overrides)
    return await _post(ctx, _pbx_url(ctx, "add/createTenant"), payload, "create_tenant")


async def create_extension(
    ctx,
    tenant_id: str,
    ext_number: str | list[str],
    plan_id: str = "7",
    shift_id: str = "7",
    ext_callgroup: list[str] | None = None,
    ext_sip_password: str = "",
    ext_web_password: str = "",
    ext_email_id: str = "",
    language: str = "en",
    ext_max_ext_call: str = "0",
    ext_ring_timeout: str = "60",
    ext_dial_timeout: str = "60",
    ext_feature_code_pin: str = "123456",
    ext_codec: str = "PCMA,PCMU",
    timezone_id: str = "1",
) -> Any:
    """Create one or more SIP extensions for a tenant."""
    cfg = _resolve_config(ctx)
    if not cfg["pbx_token_id"]:
        raise RuntimeError("pbx_token_id is required in tool settings/credential")
    numbers = ext_number if isinstance(ext_number, list) else [ext_number]
    payload: dict[str, Any] = {
        "token_id": cfg["pbx_token_id"],
        "tenant_id": tenant_id,
        "plan_id": plan_id,
        "shift_id": shift_id,
        "ext_number": numbers,
        "ext_callgroup": ext_callgroup if ext_callgroup else ["8"],
        "language": language,
        "ext_sip_password": ext_sip_password,
        "ext_web_password": ext_web_password,
        "ext_email_id": ext_email_id,
        "ext_max_ext_call": ext_max_ext_call,
        "ext_ring_timeout": ext_ring_timeout,
        "ext_dial_timeout": ext_dial_timeout,
        "ext_feature_code_pin": ext_feature_code_pin,
        "ext_codec": ext_codec,
        "timezone_id": timezone_id,
    }
    return await _post(ctx, _pbx_url(ctx, "add/createExtension"), payload, "create_extension")


async def create_did(
    ctx,
    tenant_id: str,
    number: str | list[str],
    ven_id: str = "1",
    buy_rate_plan_id: str = "7",
    circle_id: str = "1",
    sell_rate_plan_id: str = "8",
    did_type: str = "3",
) -> Any:
    """Allocate one or more DID numbers to a tenant."""
    cfg = _resolve_config(ctx)
    if not cfg["pbx_token_id"]:
        raise RuntimeError("pbx_token_id is required in tool settings/credential")
    numbers = number if isinstance(number, list) else [number]
    payload = {
        "token_id": cfg["pbx_token_id"],
        "tenant_id": tenant_id,
        "number": numbers,
        "ven_id": ven_id,
        "buy_rate_plan_id": buy_rate_plan_id,
        "circle_id": circle_id,
        "sell_rate_plan_id": sell_rate_plan_id,
        "did_type": did_type,
    }
    return await _post(ctx, _pbx_url(ctx, "add/createDid"), payload, "create_did")


async def add_balance(ctx, tenant_id: str, amount: str, remark: str = "") -> Any:
    """Top up a tenant's account balance."""
    cfg = _resolve_config(ctx)
    if not cfg["pbx_token_id"]:
        raise RuntimeError("pbx_token_id is required in tool settings/credential")
    payload = {
        "token_id": cfg["pbx_token_id"],
        "tenant_id": tenant_id,
        "amount": amount,
        "remark": remark,
    }
    return await _post(ctx, _pbx_url(ctx, "add/addBalance"), payload, "add_balance")


async def get_billplan(ctx) -> Any:
    """List available bill plans (resolve tenant_billplan_id for create_tenant)."""
    cfg = _resolve_config(ctx)
    if not cfg["pbx_token_id"]:
        raise RuntimeError("pbx_token_id is required in tool settings/credential")
    return await _post(
        ctx,
        _pbx_url(ctx, "info/getBillplan"),
        {"token_id": cfg["pbx_token_id"]},
        "get_billplan",
    )


async def get_rateplan(ctx) -> Any:
    """List available rate plans (resolve buy/sell rate plan ids for create_did)."""
    cfg = _resolve_config(ctx)
    if not cfg["pbx_token_id"]:
        raise RuntimeError("pbx_token_id is required in tool settings/credential")
    return await _post(
        ctx,
        _pbx_url(ctx, "info/getRateplan"),
        {"token_id": cfg["pbx_token_id"]},
        "get_rateplan",
    )


async def get_og_rule(ctx) -> Any:
    """List outgoing rule groups (resolve rule_group_id for create_tenant)."""
    cfg = _resolve_config(ctx)
    if not cfg["pbx_token_id"]:
        raise RuntimeError("pbx_token_id is required in tool settings/credential")
    return await _post(
        ctx,
        _pbx_url(ctx, "info/getOGRule"),
        {"token_id": cfg["pbx_token_id"]},
        "get_og_rule",
    )


async def get_did_details(ctx) -> Any:
    """List the DID inventory / details visible to this token."""
    cfg = _resolve_config(ctx)
    if not cfg["pbx_token_id"]:
        raise RuntimeError("pbx_token_id is required in tool settings/credential")
    return await _post(
        ctx,
        _pbx_url(ctx, "info/getDID"),
        {"token_id": cfg["pbx_token_id"]},
        "get_did_details",
    )


async def get_balance(ctx) -> Any:
    """Get the admin balance summary."""
    cfg = _resolve_config(ctx)
    if not cfg["pbx_token_id"]:
        raise RuntimeError("pbx_token_id is required in tool settings/credential")
    return await _post(
        ctx,
        _pbx_url(ctx, "info/ADMIN/balance"),
        {"token_id": cfg["pbx_token_id"]},
        "get_balance",
    )


async def get_active_calls(ctx) -> Any:
    """List live/active calls right now."""
    cfg = _resolve_config(ctx)
    if not cfg["pbx_token_id"]:
        raise RuntimeError("pbx_token_id is required in tool settings/credential")
    return await _post(
        ctx,
        _pbx_url(ctx, "info/ADMIN/activeCalls"),
        {"token_id": cfg["pbx_token_id"]},
        "get_active_calls",
    )


async def get_call_log(
    ctx,
    from_datetime: str,
    to_datetime: str,
    scope: str = "Tenant",
    unique_token: str = "",
) -> Any:
    """Fetch historical call logs (CDR) for a date/time range."""
    cfg = _resolve_config(ctx)
    if not cfg["ccpl_token_id"]:
        raise RuntimeError("ccpl_token_id is required in tool settings/credential")
    token = unique_token or cfg["ccpl_unique_token"]
    if not token:
        raise RuntimeError("ccpl_unique_token is required for get_call_log")
    path = f"info/{from_datetime}/{to_datetime}/{scope}/callLog"
    payload = {"token_id": cfg["ccpl_token_id"], "unique_token": token}
    return await _post(ctx, _ccpl_url(ctx, path), payload, "get_call_log")
'''

CC_PBX_DEFAULT_CAPABILITIES = [
    {
        "name": "create_tenant",
        "description": "Provision a new PBX tenant (customer account)",
        "mutating": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant_name": {"type": "string"},
                "tenant_username": {"type": "string"},
                "tenant_password": {"type": "string"},
                "tenant_email": {"type": "string"},
                "first_name": {"type": "string", "default": ""},
                "last_name": {"type": "string", "default": ""},
                "business_name": {"type": "string", "default": ""},
                "phone_number": {"type": "string", "default": ""},
                "address1": {"type": "string", "default": ""},
                "city": {"type": "string", "default": ""},
                "state": {"type": "string", "default": ""},
                "zip": {"type": "string", "default": ""},
                "tenant_billplan_id": {"type": "string", "default": "2"},
                "rule_group_id": {"type": "string", "default": "115"},
                "tenant_currency": {"type": "string", "default": "64"},
                "tenant_country": {"type": "string", "default": "99"},
                "timezone_id": {"type": "string", "default": "472"},
                "tenant_bill_type": {"type": "string", "default": "POSTPAID"},
                "overrides": {"type": "object", "default": {}},
            },
            "required": [
                "tenant_name",
                "tenant_username",
                "tenant_password",
                "tenant_email",
            ],
        },
    },
    {
        "name": "create_extension",
        "description": "Create one or more SIP extensions for a tenant",
        "mutating": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string"},
                "ext_number": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ]
                },
                "plan_id": {"type": "string", "default": "7"},
                "shift_id": {"type": "string", "default": "7"},
                "ext_callgroup": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ["8"],
                },
                "ext_sip_password": {"type": "string", "default": ""},
                "ext_web_password": {"type": "string", "default": ""},
                "ext_email_id": {"type": "string", "default": ""},
                "language": {"type": "string", "default": "en"},
                "ext_max_ext_call": {"type": "string", "default": "0"},
                "ext_ring_timeout": {"type": "string", "default": "60"},
                "ext_dial_timeout": {"type": "string", "default": "60"},
                "ext_feature_code_pin": {"type": "string", "default": "123456"},
                "ext_codec": {"type": "string", "default": "PCMA,PCMU"},
                "timezone_id": {"type": "string", "default": "1"},
            },
            "required": ["tenant_id", "ext_number"],
        },
    },
    {
        "name": "create_did",
        "description": "Allocate one or more DID numbers to a tenant",
        "mutating": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string"},
                "number": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ]
                },
                "ven_id": {"type": "string", "default": "1"},
                "buy_rate_plan_id": {"type": "string", "default": "7"},
                "circle_id": {"type": "string", "default": "1"},
                "sell_rate_plan_id": {"type": "string", "default": "8"},
                "did_type": {"type": "string", "default": "3"},
            },
            "required": ["tenant_id", "number"],
        },
    },
    {
        "name": "add_balance",
        "description": "Top up a tenant's account balance",
        "mutating": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string"},
                "amount": {"type": "string"},
                "remark": {"type": "string", "default": ""},
            },
            "required": ["tenant_id", "amount"],
        },
    },
    {
        "name": "get_billplan",
        "description": "List available bill plans",
        "mutating": False,
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_rateplan",
        "description": "List available rate plans",
        "mutating": False,
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_og_rule",
        "description": "List outgoing rule groups",
        "mutating": False,
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_did_details",
        "description": "List DID inventory visible to this token",
        "mutating": False,
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_balance",
        "description": "Get the admin balance summary",
        "mutating": False,
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_active_calls",
        "description": "List live/active calls",
        "mutating": False,
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_call_log",
        "description": "Fetch historical call logs (CDR) for a date/time range",
        "mutating": False,
        "input_schema": {
            "type": "object",
            "properties": {
                "from_datetime": {
                    "type": "string",
                    "description": 'Range start, e.g. "2024-11-15-00:00"',
                },
                "to_datetime": {
                    "type": "string",
                    "description": 'Range end, e.g. "2024-11-15-23:59"',
                },
                "scope": {"type": "string", "default": "Tenant"},
                "unique_token": {"type": "string", "default": ""},
            },
            "required": ["from_datetime", "to_datetime"],
        },
    },
]

CC_PBX_DEFAULT_SETTINGS = {
    "base_url": "https://dev2.cloud-connect.in",
    "pbx_api_root": "hodupbx_api/v1.4/api",
    "ccpl_api_root": "ccpl_api/v1.4/api",
    "timeout": 60,
}
