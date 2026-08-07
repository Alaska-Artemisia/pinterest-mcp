"""
Me + Lia - Pinterest MCP Server (v3)
Exposes Pinterest v5 API actions to Claude via MCP protocol.

v3 adds the write/targeting/audience/conversion surface that v2 was missing:
  - create_campaign                  (no more "make the shell in the UI")
  - create_ad_group  EXTENDED        (full targeting_spec: age/gender/locale/
                                      interests/audience include+exclude,
                                      placement_group, expanded targeting)
  - update_ad_group / update_ad      (flip status, budget, targeting from here)
  - create_keywords                  (human-readable keyword targeting)
  - create_customer_list /
    update_customer_list /
    list_audiences /
    create_actalike_audience         (customer-list match + Actalike seed)
  - get_conversion_tags /
    send_conversion_event            (read tags; fire server-side Signup events)

Auth: uses a long-lived REFRESH TOKEN to mint short-lived access tokens
automatically, so tokens never go stale. Capture the refresh token once by
visiting /login in a browser (see README / setup notes).

Env vars:
  PINTEREST_CLIENT_ID       - app id (e.g. 1571876)
  PINTEREST_CLIENT_SECRET   - app secret
  PINTEREST_REFRESH_TOKEN   - long-lived refresh token (from /login)
  PINTEREST_ACCESS_TOKEN    - optional static token override (legacy/testing)
  PORT                      - set by host (Railway/Render)

VERIFY-AGAINST-DOCS NOTES (flagged inline with #! ):
  A few v5 request shapes evolve over time — the spots marked #! are the ones
  to sanity-check against current Pinterest v5 docs if a call 400s. Everything
  else mirrors the bulk-array / PATCH-array patterns already proven in v2.
"""

import os
import json
import time
import base64
import hashlib
from urllib.parse import urlparse, parse_qs

import httpx
from http.server import HTTPServer, BaseHTTPRequestHandler

PINTEREST_API = "https://api.pinterest.com/v5"
AUTHORIZE_URL = "https://www.pinterest.com/oauth/"
SCOPES = "ads:read,ads:write,boards:read,boards:write,pins:read,pins:write,user_accounts:read"

CLIENT_ID = os.environ.get("PINTEREST_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("PINTEREST_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("PINTEREST_REFRESH_TOKEN", "")
STATIC_TOKEN = os.environ.get("PINTEREST_ACCESS_TOKEN", "")  # optional override
PORT = int(os.environ.get("PORT", 8080))

# In-memory access-token cache: {"access_token": str, "expires_at": epoch}
_token_cache = {"access_token": "", "expires_at": 0.0}


# ----------------------------------------------------------------------------
# Auth: refresh-token flow
# ----------------------------------------------------------------------------
def _basic_auth_header():
    raw = f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def refresh_access_token():
    """Exchange the refresh token for a fresh access token; cache it."""
    if not REFRESH_TOKEN:
        raise RuntimeError(
            "No PINTEREST_REFRESH_TOKEN set. Visit /login once to authorize, "
            "then paste the refresh token into the host's env vars."
        )
    r = httpx.post(
        f"{PINTEREST_API}/oauth/token",
        headers={
            "Authorization": _basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "refresh_token", "refresh_token": REFRESH_TOKEN},
        timeout=30,
    )
    r.raise_for_status()
    j = r.json()
    _token_cache["access_token"] = j["access_token"]
    # refresh slightly early; default 30d if not provided
    _token_cache["expires_at"] = time.time() + int(j.get("expires_in", 2592000))
    return _token_cache["access_token"]


def get_access_token():
    if STATIC_TOKEN:
        return STATIC_TOKEN
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]
    return refresh_access_token()


def auth_headers():
    return {"Authorization": f"Bearer {get_access_token()}", "Content-Type": "application/json"}


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------
def _sha256_email(email):
    """Normalize (lowercase + trim) and SHA-256 hash an email, per CAPI spec."""
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def _normalize_emails(emails):
    """Lowercase, trim, dedupe. Returns a list of clean emails."""
    seen = []
    for e in emails or []:
        if not e:
            continue
        v = e.strip().lower()
        if v and v not in seen:
            seen.append(v)
    return seen


def _build_targeting_spec(country=None, age_buckets=None, genders=None, locales=None,
                          interest_ids=None, audience_include=None, audience_exclude=None,
                          extra=None):
    """Assemble a Pinterest v5 targeting_spec from convenience args.

    Keys map to v5 targeting_spec fields. `extra` is a raw passthrough dict that
    merges last, so any key Pinterest supports can be supplied directly even if
    there is no convenience arg for it.
    """
    spec = {}
    if country:
        spec["GEO"] = [country] if isinstance(country, str) else list(country)
    if age_buckets:
        spec["AGE_BUCKET"] = age_buckets            # e.g. ["25-34","35-44"]
    if genders:
        spec["GENDER"] = genders                    # e.g. ["female"]
    if locales:
        spec["LOCALE"] = locales                    # e.g. ["en-US"]
    if interest_ids:
        spec["INTEREST"] = interest_ids             # taxonomy interest IDs
    if audience_include:
        spec["AUDIENCE_INCLUDE"] = audience_include  # audience IDs
    if audience_exclude:
        spec["AUDIENCE_EXCLUDE"] = audience_exclude  # audience IDs
    if isinstance(extra, dict):
        for k, v in extra.items():
            spec[k] = v
    return spec


# ----------------------------------------------------------------------------
# Boards & Pins
# ----------------------------------------------------------------------------
def list_boards():
    r = httpx.get(f"{PINTEREST_API}/boards", headers=auth_headers(), params={"page_size": 100}, timeout=30)
    r.raise_for_status()
    return r.json().get("items", [])


def create_board(name, description="", privacy="PUBLIC"):
    r = httpx.post(f"{PINTEREST_API}/boards", headers=auth_headers(),
                   json={"name": name, "description": description, "privacy": privacy}, timeout=30)
    r.raise_for_status()
    return r.json()


def create_pin(board_id, title, description, image_url, link="https://meandlia.com", alt_text=""):
    payload = {
        "board_id": board_id,
        "title": title,
        "description": description,
        "link": link,
        "alt_text": alt_text or title,
        "media_source": {"source_type": "image_url", "url": image_url},
    }
    r = httpx.post(f"{PINTEREST_API}/pins", headers=auth_headers(), json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def list_pins(board_id, max_pins=250):
    """All pins on a board, following the bookmark. Returns FULL pin objects."""
    items, bookmark = [], None
    while len(items) < max_pins:
        params = {"page_size": 100}
        if bookmark:
            params["bookmark"] = bookmark
        r = httpx.get(f"{PINTEREST_API}/boards/{board_id}/pins", headers=auth_headers(),
                      params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
        items.extend(body.get("items", []))
        bookmark = body.get("bookmark")
        if not bookmark:
            break
    return items[:max_pins]


def get_pin(pin_id):
    """Full detail for one pin, including its destination link."""
    r = httpx.get(f"{PINTEREST_API}/pins/{pin_id}", headers=auth_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def _guard_pin(pin_id, expected_title, expect_board_id=None):
    """Refuse to touch a pin unless title (and optionally board) match. Returns the live pin."""
    live = get_pin(pin_id)
    actual = (live.get("title") or "").strip()
    if actual != (expected_title or "").strip():
        raise ValueError(
            f"GUARD FAILED: pin {pin_id} is titled {actual!r}, expected "
            f"{(expected_title or '')!r}. Nothing was changed."
        )
    if expect_board_id and str(live.get("board_id")) != str(expect_board_id):
        raise ValueError(
            f"GUARD FAILED: pin {pin_id} is on board {live.get('board_id')}, "
            f"expected {expect_board_id}. Nothing was changed."
        )
    return live


def update_pin(pin_id, expected_title, link=None, title=None, description=None,
               alt_text=None, expect_board_id=None):
    """PATCH a pin's link/title/description. NOTE: v5 PATCH /pins is beta-gated; may 403."""
    _guard_pin(pin_id, expected_title, expect_board_id)
    payload = {k: v for k, v in {"link": link, "title": title,
                                 "description": description, "alt_text": alt_text}.items()
               if v is not None}
    if not payload:
        raise ValueError("Nothing to update - pass at least one of link/title/description/alt_text.")
    r = httpx.patch(f"{PINTEREST_API}/pins/{pin_id}", headers=auth_headers(),
                    json=payload, timeout=30)
    if r.status_code in (403, 404) and "beta" in (r.text or "").lower():
        raise RuntimeError(
            f"PATCH /pins is not enabled for this app (HTTP {r.status_code}). "
            f"Pin edits must be done in the Pinterest UI. Body: {r.text[:300]}"
        )
    r.raise_for_status()
    after = get_pin(pin_id)
    return {"pin": after, "verified": all(str(after.get(k)) == str(v) for k, v in payload.items())}


def delete_pin(pin_id, expected_title, expect_board_id=None):
    """PERMANENT. Pinterest has no trash and no undo. Guarded on title (+ optional board)."""
    live = _guard_pin(pin_id, expected_title, expect_board_id)
    r = httpx.delete(f"{PINTEREST_API}/pins/{pin_id}", headers=auth_headers(), timeout=30)
    r.raise_for_status()
    return {"deleted": pin_id, "title": (live.get("title") or ""), "board_id": live.get("board_id")}


def get_pin_analytics(pin_ids, start_date, end_date, ad_account_id=None):
    """Organic pin performance. Batch endpoint, max 100 pins. Dates YYYY-MM-DD, max 90d window."""
    if isinstance(pin_ids, str):
        pin_ids = [pin_ids]
    if len(pin_ids) > 100:
        raise ValueError("Max 100 pin_ids per call.")
    params = {
        "pin_ids": ",".join(str(p) for p in pin_ids),
        "start_date": start_date,
        "end_date": end_date,
        "metric_types": "IMPRESSION,PIN_CLICK,OUTBOUND_CLICK,SAVE",
    }
    if ad_account_id:
        params["ad_account_id"] = ad_account_id
    r = httpx.get(f"{PINTEREST_API}/pins/analytics", headers=auth_headers(),
                  params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def get_account_info():
    r = httpx.get(f"{PINTEREST_API}/user_account", headers=auth_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


# ----------------------------------------------------------------------------
# Ads: read
# ----------------------------------------------------------------------------
def get_ad_accounts():
    r = httpx.get(f"{PINTEREST_API}/ad_accounts", headers=auth_headers(), params={"page_size": 25}, timeout=30)
    r.raise_for_status()
    return r.json().get("items", [])


def get_campaigns(ad_account_id):
    r = httpx.get(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/campaigns",
                  headers=auth_headers(), params={"page_size": 25}, timeout=30)
    r.raise_for_status()
    return r.json().get("items", [])


def get_ad_groups(ad_account_id, campaign_id=None):
    params = {"page_size": 25}
    if campaign_id:
        params["campaign_ids"] = campaign_id
    r = httpx.get(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/ad_groups",
                  headers=auth_headers(), params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("items", [])


# ----------------------------------------------------------------------------
# Ads: write
# ----------------------------------------------------------------------------
def create_campaign(ad_account_id, name, objective_type="CONSIDERATION",
                    daily_budget_micro=None, lifetime_budget_micro=None, status="PAUSED"):
    """Create a campaign. Created PAUSED by default so nothing spends until reviewed.

    objective_type: AWARENESS | CONSIDERATION | VIDEO_VIEW | WEB_CONVERSION |
                    CATALOG_SALES  (CONSIDERATION = traffic/clicks)
    Budget at campaign level => CBO; ad groups inherit it.
    """
    campaign = {"name": name, "objective_type": objective_type, "status": status}
    if daily_budget_micro is not None:
        campaign["daily_spend_cap"] = int(daily_budget_micro)
    if lifetime_budget_micro is not None:
        campaign["lifetime_spend_cap"] = int(lifetime_budget_micro)
    r = httpx.post(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/campaigns",
                   headers=auth_headers(), json=[campaign], timeout=30)
    r.raise_for_status()
    return r.json()


def update_campaign(ad_account_id, campaign_id, status=None, name=None, daily_budget_micro=None):
    """Update a campaign: pause/resume (status), rename (name), or change daily
    budget (daily_budget_micro). Only provided fields are changed."""
    obj = {"id": str(campaign_id)}
    if status is not None:
        obj["status"] = status
    if name is not None:
        obj["name"] = name
    if daily_budget_micro is not None:
        obj["daily_spend_cap"] = int(daily_budget_micro)
    r = httpx.patch(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/campaigns",
                    headers=auth_headers(), json=[obj], timeout=30)
    r.raise_for_status()
    return r.json()


def create_ad_group(ad_account_id, campaign_id, name, country="US",
                    daily_budget_micro=None, bid_micro=None, billing_event="IMPRESSION",
                    status="PAUSED", age_buckets=None, genders=None, locales=None,
                    interest_ids=None, audience_include=None, audience_exclude=None,
                    placement_group=None, auto_targeting=True, targeting_spec=None):
    """Create an ad group under a campaign. Created PAUSED by default.

    Targeting: pass convenience args (country/age_buckets/genders/locales/
    interest_ids/audience_include/audience_exclude) and/or a raw `targeting_spec`
    dict that merges last. `auto_targeting` = Pinterest's expanded targeting.
    For CBO (Consideration/Web Conversion) campaigns the budget is inherited from
    the campaign and daily_budget_micro can be omitted.
    """
    spec = _build_targeting_spec(country=country, age_buckets=age_buckets, genders=genders,
                                 locales=locales, interest_ids=interest_ids,
                                 audience_include=audience_include, audience_exclude=audience_exclude,
                                 extra=targeting_spec)
    ad_group = {
        "campaign_id": campaign_id,
        "name": name,
        "status": status,
        "billable_event": billing_event,
        "auto_targeting_enabled": bool(auto_targeting),
        "targeting_spec": spec,
    }
    if placement_group is not None:
        ad_group["placement_group"] = placement_group   # ALL | SEARCH | BROWSE | OTHER
    if daily_budget_micro is not None:
        ad_group["budget_in_micro_currency"] = int(daily_budget_micro)
        ad_group["budget_type"] = "DAILY"
    if bid_micro is not None:
        ad_group["bid_in_micro_currency"] = int(bid_micro)
    r = httpx.post(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/ad_groups",
                   headers=auth_headers(), json=[ad_group], timeout=30)
    r.raise_for_status()
    return r.json()


def update_ad_group(ad_account_id, ad_group_id, status=None, name=None,
                    daily_budget_micro=None, bid_micro=None, auto_targeting=None,
                    placement_group=None, targeting_spec=None):
    """Update an ad group: status, name, budget, bid, expanded targeting,
    placement, or replace targeting_spec. Only provided fields change."""
    obj = {"id": str(ad_group_id)}
    if status is not None:
        obj["status"] = status
    if name is not None:
        obj["name"] = name
    if daily_budget_micro is not None:
        obj["budget_in_micro_currency"] = int(daily_budget_micro)
        obj["budget_type"] = "DAILY"
    if bid_micro is not None:
        obj["bid_in_micro_currency"] = int(bid_micro)
    if auto_targeting is not None:
        obj["auto_targeting_enabled"] = bool(auto_targeting)
    if placement_group is not None:
        obj["placement_group"] = placement_group
    if targeting_spec is not None:
        obj["targeting_spec"] = targeting_spec
    r = httpx.patch(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/ad_groups",
                    headers=auth_headers(), json=[obj], timeout=30)
    r.raise_for_status()
    return r.json()


def create_ad(ad_account_id, ad_group_id, pin_id, name, status="PAUSED", creative_type="REGULAR"):
    # Created PAUSED by default. Body is an ARRAY of ad objects (v5 bulk create).
    payload = [{
        "ad_group_id": ad_group_id,
        "creative_type": creative_type,
        "pin_id": pin_id,
        "name": name,
        "status": status,
    }]
    r = httpx.post(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/ads",
                   headers=auth_headers(), json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def update_ad(ad_account_id, ad_id, status=None, name=None):
    """Update an ad: flip status (ACTIVE/PAUSED) or rename. Only provided fields change."""
    obj = {"id": str(ad_id)}
    if status is not None:
        obj["status"] = status
    if name is not None:
        obj["name"] = name
    r = httpx.patch(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/ads",
                    headers=auth_headers(), json=[obj], timeout=30)
    r.raise_for_status()
    return r.json()


def create_keywords(ad_account_id, ad_group_id, values, match_type="BROAD", bid_micro=None):
    """Add keyword targeting to an ad group. match_type: BROAD | PHRASE | EXACT."""
    kws = []
    for v in values:
        k = {"ad_group_id": str(ad_group_id), "parent_type": "AD_GROUP",
             "value": v, "match_type": match_type}   #! verify keyword body shape vs v5 docs
        if bid_micro is not None:
            k["bid"] = int(bid_micro)
        kws.append(k)
    r = httpx.post(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/keywords",
                   headers=auth_headers(), json={"parent_id": str(ad_group_id), "keywords": kws}, timeout=30)
    r.raise_for_status()
    return r.json()


# ----------------------------------------------------------------------------
# Audiences: customer lists + actalikes
# ----------------------------------------------------------------------------
def create_customer_list(ad_account_id, name, emails, list_type="EMAIL"):
    """Create a Customer List (and its backing audience) from first-party emails.

    Emails are normalized (lowercase + trim) and sent with list_type=EMAIL;
    Pinterest hashes them server-side. This sends YOUR first-party customer
    emails to YOUR ad account for Customer List Match.
    """
    records = ",".join(_normalize_emails(emails))
    body = {"name": name, "records": records, "list_type": list_type}
    r = httpx.post(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/customer_lists",
                   headers=auth_headers(), json=body, timeout=60)
    r.raise_for_status()
    return r.json()


def update_customer_list(ad_account_id, customer_list_id, emails, operation_type="ADD"):
    """Add or remove records on an existing customer list. operation_type: ADD | REMOVE."""
    records = ",".join(_normalize_emails(emails))
    body = {"records": records, "operation_type": operation_type}
    r = httpx.post(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/customer_lists/{customer_list_id}",
                   headers=auth_headers(), json=body, timeout=60)
    r.raise_for_status()
    return r.json()


def list_audiences(ad_account_id):
    r = httpx.get(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/audiences",
                  headers=auth_headers(), params={"page_size": 100}, timeout=30)
    r.raise_for_status()
    return r.json().get("items", [])


def create_actalike_audience(ad_account_id, name, seed_audience_id, country="US", percentage=5):
    """Create an Actalike (lookalike) audience seeded from an existing audience
    (e.g. a customer list). percentage 1-10 (smaller = closer match)."""
    body = {
        "name": name,
        "audience_type": "ACTALIKE",
        "rule": {                                   #! verify ACTALIKE rule shape vs v5 docs
            "source_id": str(seed_audience_id),
            "percentage": int(percentage),
            "country": country,
        },
    }
    r = httpx.post(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/audiences",
                   headers=auth_headers(), json=body, timeout=30)
    r.raise_for_status()
    return r.json()


# ----------------------------------------------------------------------------
# Conversions: read tags + send server-side (CAPI) events
# ----------------------------------------------------------------------------
def get_conversion_tags(ad_account_id):
    r = httpx.get(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/conversion_tags",
                  headers=auth_headers(), params={"page_size": 100}, timeout=30)
    r.raise_for_status()
    return r.json().get("items", [])


def send_conversion_event(ad_account_id, event_name, email=None, event_id=None,
                          event_time=None, action_source="web", custom_data=None):
    """Send a server-side conversion event via the Pinterest Conversions API.

    Use this to feed Pinterest a 'signup' (lead) event when someone completes the
    Slow Wardrobe guide form — e.g. called from a Klaviyo/Shopify webhook relay.
    event_name examples: signup | lead | add_to_cart | checkout | page_visit
    """
    user_data = {}
    if email:
        user_data["em"] = [_sha256_email(email)]   # CAPI requires hashed PII
    event = {
        "event_name": event_name,
        "action_source": action_source,
        "event_time": int(event_time or time.time()),
        "event_id": event_id or f"{event_name}-{int(time.time() * 1000)}",
        "user_data": user_data,
    }
    if custom_data:
        event["custom_data"] = custom_data
    body = {"data": [event]}                        #! verify CAPI /events body vs v5 docs
    r = httpx.post(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/events",
                   headers=auth_headers(), json=body, timeout=30)
    r.raise_for_status()
    return r.json()


# ----------------------------------------------------------------------------
# Ads: analytics  (validated v5 column names)
# ----------------------------------------------------------------------------
_ANALYTICS_COLS = "SPEND_IN_DOLLAR,IMPRESSION_1,CLICKTHROUGH_1,OUTBOUND_CLICK_1,CTR,TOTAL_CONVERSIONS"


def _analytics(path, ad_account_id, start_date, end_date, id_param=None, ids=None):
    params = {"start_date": start_date, "end_date": end_date,
              "columns": _ANALYTICS_COLS, "granularity": "TOTAL"}
    if id_param and ids:
        params[id_param] = ",".join(ids)
    r = httpx.get(f"{PINTEREST_API}/ad_accounts/{ad_account_id}/{path}/analytics",
                  headers=auth_headers(), params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def get_campaign_analytics(ad_account_id, start_date, end_date, campaign_ids=None):
    return _analytics("campaigns", ad_account_id, start_date, end_date, "campaign_ids", campaign_ids)


def get_ad_group_analytics(ad_account_id, start_date, end_date, ad_group_ids=None):
    return _analytics("ad_groups", ad_account_id, start_date, end_date, "ad_group_ids", ad_group_ids)


def get_ad_analytics(ad_account_id, start_date, end_date, ad_ids=None):
    return _analytics("ads", ad_account_id, start_date, end_date, "ad_ids", ad_ids)


# ----------------------------------------------------------------------------
# MCP tool registry
# ----------------------------------------------------------------------------
TOOLS = {
    "list_boards": {"description": "List all Pinterest boards", "inputSchema": {"type": "object", "properties": {}}},
    "create_board": {"description": "Create a Pinterest board", "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "description": {"type": "string"}, "privacy": {"type": "string"}}, "required": ["name"]}},
    "create_pin": {"description": "Create a Pinterest pin on a board. image_url must be a public HTTPS URL.", "inputSchema": {"type": "object", "properties": {"board_id": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"}, "image_url": {"type": "string"}, "link": {"type": "string"}, "alt_text": {"type": "string"}}, "required": ["board_id", "title", "description", "image_url"]}},
    "list_pins": {"description": "List pins on a board. Returns id, title AND destination link for every pin, paging past the first 100.", "inputSchema": {"type": "object", "properties": {"board_id": {"type": "string"}, "max_pins": {"type": "integer", "description": "Cap on pins returned (default 250)"}}, "required": ["board_id"]}},
    "get_pin": {"description": "Full detail for ONE pin: destination link, board, title, description, created_at. Use this to verify a pin's link without opening a browser.", "inputSchema": {"type": "object", "properties": {"pin_id": {"type": "string"}}, "required": ["pin_id"]}},
    "update_pin": {"description": "Update a pin's link/title/description/alt_text. GUARDED: expected_title must match the live pin exactly or the call is refused. NOTE: Pinterest v5 PATCH /pins is beta-gated and may return 403 - if so, pin edits must be done in the UI.", "inputSchema": {"type": "object", "properties": {"pin_id": {"type": "string"}, "expected_title": {"type": "string", "description": "Must match the live pin title exactly. Safety guard - pass \"\" for blank-titled pins and also pass expect_board_id."}, "link": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"}, "alt_text": {"type": "string"}, "expect_board_id": {"type": "string", "description": "Optional extra guard: refuse unless the pin is on this board."}}, "required": ["pin_id", "expected_title"]}},
    "delete_pin": {"description": "PERMANENTLY delete a pin. No trash, no undo, and any ad promoting the pin dies with it. GUARDED: expected_title must match the live pin exactly. For blank-titled pins pass expected_title=\"\" AND expect_board_id.", "inputSchema": {"type": "object", "properties": {"pin_id": {"type": "string"}, "expected_title": {"type": "string", "description": "Must match the live pin title exactly, or the delete is refused."}, "expect_board_id": {"type": "string", "description": "Optional extra guard: refuse unless the pin is on this board. REQUIRED in practice for blank-titled pins."}}, "required": ["pin_id", "expected_title"]}},
    "get_pin_analytics": {"description": "ORGANIC pin performance (impressions, pin clicks, outbound clicks, saves) for up to 100 pins in one call. Dates YYYY-MM-DD, max 90-day window. This is organic reach - NOT the paid ad analytics tools.", "inputSchema": {"type": "object", "properties": {"pin_ids": {"type": "array", "items": {"type": "string"}}, "start_date": {"type": "string"}, "end_date": {"type": "string"}, "ad_account_id": {"type": "string", "description": "Optional, for business-access accounts."}}, "required": ["pin_ids", "start_date", "end_date"]}},
    "get_account_info": {"description": "Get Pinterest account info and stats", "inputSchema": {"type": "object", "properties": {}}},
    "get_ad_accounts": {"description": "List all Pinterest ad accounts", "inputSchema": {"type": "object", "properties": {}}},
    "get_campaigns": {"description": "List all campaigns for an ad account", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}}, "required": ["ad_account_id"]}},
    "create_campaign": {"description": "Create a campaign (PAUSED by default). objective_type: AWARENESS|CONSIDERATION|VIDEO_VIEW|WEB_CONVERSION|CATALOG_SALES (CONSIDERATION = traffic/clicks). Budget at campaign level = CBO; ad groups inherit it. daily_budget_micro in micro-currency (e.g. 12 AUD = 12000000).", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "name": {"type": "string"}, "objective_type": {"type": "string", "description": "AWARENESS|CONSIDERATION|VIDEO_VIEW|WEB_CONVERSION|CATALOG_SALES"}, "daily_budget_micro": {"type": "integer"}, "lifetime_budget_micro": {"type": "integer"}, "status": {"type": "string", "description": "PAUSED (default) or ACTIVE"}}, "required": ["ad_account_id", "name"]}},
    "update_campaign": {"description": "Update a campaign: pause/resume (status=PAUSED|ACTIVE), rename (name), or change daily budget (daily_budget_micro, in micro-currency e.g. 20 AUD = 20000000). Only provided fields change.", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "campaign_id": {"type": "string"}, "status": {"type": "string", "description": "PAUSED or ACTIVE"}, "name": {"type": "string"}, "daily_budget_micro": {"type": "integer"}}, "required": ["ad_account_id", "campaign_id"]}},
    "get_ad_groups": {"description": "List ad groups for an ad account, optionally filtered to one campaign", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "campaign_id": {"type": "string"}}, "required": ["ad_account_id"]}},
    "create_ad_group": {"description": "Create an ad group under a campaign (PAUSED by default) with full targeting. Pass any of: country (2-letter), age_buckets (e.g. ['25-34','35-44']), genders (['female']), locales (['en-US']), interest_ids (taxonomy IDs), audience_include / audience_exclude (audience IDs), placement_group (ALL|SEARCH|BROWSE|OTHER), auto_targeting (expanded targeting, default true), or a raw targeting_spec object that merges last. For CBO campaigns budget is inherited; omit daily_budget_micro.", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "campaign_id": {"type": "string"}, "name": {"type": "string"}, "country": {"type": "string", "description": "Two-letter geo code, default US"}, "age_buckets": {"type": "array", "items": {"type": "string"}}, "genders": {"type": "array", "items": {"type": "string"}}, "locales": {"type": "array", "items": {"type": "string"}}, "interest_ids": {"type": "array", "items": {"type": "string"}}, "audience_include": {"type": "array", "items": {"type": "string"}}, "audience_exclude": {"type": "array", "items": {"type": "string"}}, "placement_group": {"type": "string", "description": "ALL|SEARCH|BROWSE|OTHER"}, "auto_targeting": {"type": "boolean", "description": "Expanded targeting (default true)"}, "targeting_spec": {"type": "object", "description": "Raw v5 targeting_spec; merges over convenience args"}, "daily_budget_micro": {"type": "integer", "description": "ABO only: daily budget in micro-currency"}, "bid_micro": {"type": "integer"}, "billing_event": {"type": "string", "description": "IMPRESSION or CLICKTHROUGH (CLICKTHROUGH typical for traffic)"}, "status": {"type": "string", "description": "PAUSED (default) or ACTIVE"}}, "required": ["ad_account_id", "campaign_id", "name"]}},
    "update_ad_group": {"description": "Update an ad group: status (PAUSED|ACTIVE), name, budget (daily_budget_micro), bid_micro, auto_targeting, placement_group, or replace targeting_spec. Only provided fields change.", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "ad_group_id": {"type": "string"}, "status": {"type": "string"}, "name": {"type": "string"}, "daily_budget_micro": {"type": "integer"}, "bid_micro": {"type": "integer"}, "auto_targeting": {"type": "boolean"}, "placement_group": {"type": "string"}, "targeting_spec": {"type": "object"}}, "required": ["ad_account_id", "ad_group_id"]}},
    "create_ad": {"description": "Create an ad by promoting an existing pin into an ad group. Created PAUSED by default so it does not spend until you set it live.", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "ad_group_id": {"type": "string"}, "pin_id": {"type": "string"}, "name": {"type": "string"}, "status": {"type": "string", "description": "PAUSED (default) or ACTIVE"}, "creative_type": {"type": "string", "description": "Default REGULAR (standard image pin)"}}, "required": ["ad_account_id", "ad_group_id", "pin_id", "name"]}},
    "update_ad": {"description": "Update an ad: flip status (ACTIVE/PAUSED) or rename. This is how you toggle an individual ad live or paused from the API. Only provided fields change.", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "ad_id": {"type": "string"}, "status": {"type": "string", "description": "ACTIVE or PAUSED"}, "name": {"type": "string"}}, "required": ["ad_account_id", "ad_id"]}},
    "create_keywords": {"description": "Add keyword targeting to an ad group. values = list of phrases (human-readable, no ID lookup). match_type: BROAD|PHRASE|EXACT.", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "ad_group_id": {"type": "string"}, "values": {"type": "array", "items": {"type": "string"}}, "match_type": {"type": "string", "description": "BROAD|PHRASE|EXACT"}, "bid_micro": {"type": "integer"}}, "required": ["ad_account_id", "ad_group_id", "values"]}},
    "create_customer_list": {"description": "Create a Customer List (and backing audience) from first-party emails. Emails are normalized and Pinterest hashes them. Use as an Actalike seed or as an audience_exclude (NOT as the cold audience itself).", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "name": {"type": "string"}, "emails": {"type": "array", "items": {"type": "string"}}, "list_type": {"type": "string", "description": "EMAIL (default)"}}, "required": ["ad_account_id", "name", "emails"]}},
    "update_customer_list": {"description": "Add or remove emails on an existing customer list. operation_type: ADD|REMOVE.", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "customer_list_id": {"type": "string"}, "emails": {"type": "array", "items": {"type": "string"}}, "operation_type": {"type": "string", "description": "ADD|REMOVE"}}, "required": ["ad_account_id", "customer_list_id", "emails"]}},
    "list_audiences": {"description": "List audiences (customer lists, actalikes, visitor/engagement audiences) for an ad account, with their IDs and types.", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}}, "required": ["ad_account_id"]}},
    "create_actalike_audience": {"description": "Create an Actalike (lookalike) audience seeded from an existing audience id (e.g. a customer list). percentage 1-10, smaller = closer match.", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "name": {"type": "string"}, "seed_audience_id": {"type": "string"}, "country": {"type": "string"}, "percentage": {"type": "integer"}}, "required": ["ad_account_id", "name", "seed_audience_id"]}},
    "get_conversion_tags": {"description": "List conversion tags (the Pinterest tag) and their configured events for an ad account. Use to confirm whether a Signup/Lead event exists.", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}}, "required": ["ad_account_id"]}},
    "send_conversion_event": {"description": "Send a server-side conversion event via the Pinterest Conversions API. Use to feed a 'signup' (lead) event when the Slow Wardrobe form is completed (e.g. from a Klaviyo/Shopify webhook). email is hashed before sending. event_name examples: signup|lead|add_to_cart|checkout|page_visit.", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "event_name": {"type": "string"}, "email": {"type": "string"}, "event_id": {"type": "string"}, "event_time": {"type": "integer", "description": "Unix seconds; defaults to now"}, "action_source": {"type": "string", "description": "web|app_android|app_ios|offline|web_offline"}, "custom_data": {"type": "object"}}, "required": ["ad_account_id", "event_name"]}},
    "get_campaign_analytics": {"description": "Get campaign analytics: spend, impressions, clicks, CTR, conversions for a date range", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "start_date": {"type": "string", "description": "YYYY-MM-DD"}, "end_date": {"type": "string", "description": "YYYY-MM-DD"}, "campaign_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["ad_account_id", "start_date", "end_date"]}},
    "get_ad_group_analytics": {"description": "Get ad group level analytics for a date range", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "start_date": {"type": "string"}, "end_date": {"type": "string"}, "ad_group_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["ad_account_id", "start_date", "end_date"]}},
    "get_ad_analytics": {"description": "Get individual ad performance analytics for a date range", "inputSchema": {"type": "object", "properties": {"ad_account_id": {"type": "string"}, "start_date": {"type": "string"}, "end_date": {"type": "string"}, "ad_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["ad_account_id", "start_date", "end_date"]}},
}


def format_spend(micro_dollars):
    if micro_dollars is None:
        return "N/A"
    return f"${micro_dollars / 1_000_000:.2f}"


def extract_created(resp):
    items = resp.get("items") if isinstance(resp, dict) else resp
    if not items:
        return None, None
    first = items[0]
    if isinstance(first, dict):
        return first.get("data", first), first.get("exceptions")
    return first, None


def _fmt_analytics(title, data):
    if not data:
        return ok(f"No analytics data found for this period.")
    lines = [title, "=" * 40]
    rows = data if isinstance(data, list) else [data]
    for row in rows:
        metrics = row.get("metrics", row) if isinstance(row, dict) else {}
        ident = row.get("campaign_id") or row.get("ad_group_id") or row.get("ad_id") or ""
        if ident:
            lines.append(f"\nID: {ident}")
        for k, v in metrics.items():
            if k.endswith("_ID"):
                continue
            if k == "SPEND_IN_DOLLAR":
                lines.append(f"  Spend: {format_spend(v * 1_000_000) if isinstance(v, (int, float)) else v}")
            else:
                lines.append(f"  {k}: {v}")
    return ok("\n".join(lines))


def handle_mcp(method, params):
    if method == "tools/list":
        return {"tools": [{"name": k, "description": v["description"], "inputSchema": v["inputSchema"]} for k, v in TOOLS.items()]}

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {})
        try:
            if name == "list_boards":
                boards = list_boards()
                text = "\n".join([f"- {b['name']} (ID: {b['id']})" for b in boards]) or "No boards found."
                return ok(text)

            elif name == "create_board":
                board = create_board(args["name"], args.get("description", ""), args.get("privacy", "PUBLIC"))
                return ok(f"Board created: {board['name']} (ID: {board['id']})")

            elif name == "create_pin":
                pin = create_pin(args["board_id"], args["title"], args["description"], args["image_url"], args.get("link", "https://meandlia.com"), args.get("alt_text", ""))
                return ok(f"Pin created! ID: {pin['id']}\nURL: https://pinterest.com/pin/{pin['id']}/")

            elif name == "list_pins":
                pins = list_pins(args["board_id"], args.get("max_pins", 250))
                if not pins:
                    return ok("No pins found on this board.")
                lines = []
                for p in pins:
                    t = (p.get("title") or "").strip() or "(BLANK TITLE)"
                    lines.append(f"- {t} (ID: {p['id']})\n    link: {p.get('link') or '(none)'}")
                return ok(f"{len(pins)} pins\n" + "\n".join(lines))

            elif name == "get_pin":
                p = get_pin(args["pin_id"])
                return ok(
                    f"Pin {p.get('id')}\n"
                    f"  title:       {p.get('title') or '(blank)'}\n"
                    f"  link:        {p.get('link') or '(none)'}\n"
                    f"  board_id:    {p.get('board_id')}\n"
                    f"  created_at:  {p.get('created_at')}\n"
                    f"  description: {(p.get('description') or '')[:200]}"
                )

            elif name == "update_pin":
                res = update_pin(
                    args["pin_id"], args["expected_title"],
                    link=args.get("link"), title=args.get("title"),
                    description=args.get("description"), alt_text=args.get("alt_text"),
                    expect_board_id=args.get("expect_board_id"),
                )
                p = res["pin"]
                flag = "VERIFIED" if res["verified"] else "SAVED BUT READ-BACK DID NOT MATCH"
                return ok(f"Updated pin {p.get('id')} [{flag}]\n  title: {p.get('title')}\n  link:  {p.get('link')}")

            elif name == "delete_pin":
                res = delete_pin(args["pin_id"], args["expected_title"], args.get("expect_board_id"))
                return ok(f"DELETED pin {res['deleted']} (title: {res['title'] or '(blank)'}, board: {res['board_id']}). This is permanent.")

            elif name == "get_pin_analytics":
                data = get_pin_analytics(args["pin_ids"], args["start_date"], args["end_date"], args.get("ad_account_id"))
                return ok(_fmt_analytics("Organic Pin Analytics", data))

            elif name == "get_account_info":
                info = get_account_info()
                return ok(f"Username: {info.get('username')}\nAccount type: {info.get('account_type')}\nProfile: {info.get('website_url', 'N/A')}")

            elif name == "get_ad_accounts":
                accounts = get_ad_accounts()
                if not accounts:
                    return ok("No ad accounts found.")
                return ok("\n".join([f"- {a['name']} (ID: {a['id']}, Currency: {a.get('currency', 'N/A')})" for a in accounts]))

            elif name == "get_campaigns":
                campaigns = get_campaigns(args["ad_account_id"])
                if not campaigns:
                    return ok("No campaigns found.")
                lines = []
                for c in campaigns:
                    cap = c.get("daily_spend_cap")
                    cap_s = format_spend(cap) if isinstance(cap, int) else "N/A"
                    lines.append(f"- {c['name']} (ID: {c['id']}, Status: {c.get('status', 'N/A')}, Objective: {c.get('objective_type', 'N/A')}, Daily cap: {cap_s})")
                return ok("\n".join(lines))

            elif name == "create_campaign":
                resp = create_campaign(args["ad_account_id"], args["name"],
                                       args.get("objective_type", "CONSIDERATION"),
                                       args.get("daily_budget_micro"), args.get("lifetime_budget_micro"),
                                       args.get("status", "PAUSED"))
                data, exc = extract_created(resp)
                if exc:
                    return err(f"Campaign creation returned exceptions: {json.dumps(exc)}")
                d = data or {}
                return ok(f"Campaign created (PAUSED unless set otherwise)! ID: {d.get('id')} "
                          f"(objective: {d.get('objective_type', 'N/A')}, status: {d.get('status', 'N/A')})")

            elif name == "update_campaign":
                resp = update_campaign(args["ad_account_id"], args["campaign_id"],
                                       args.get("status"), args.get("name"), args.get("daily_budget_micro"))
                data, exc = extract_created(resp)
                if exc:
                    return err(f"Update returned exceptions: {json.dumps(exc)}")
                d = data or {}
                return ok(f"Campaign updated. ID: {d.get('id', args['campaign_id'])}, Status: {d.get('status', 'N/A')}, Name: {d.get('name', 'N/A')}")

            elif name == "get_ad_groups":
                ad_groups = get_ad_groups(args["ad_account_id"], args.get("campaign_id"))
                if not ad_groups:
                    return ok("No ad groups found.")
                return ok("\n".join([f"- {a.get('name', 'Unnamed')} (ID: {a['id']}, Status: {a.get('status', 'N/A')}, Campaign: {a.get('campaign_id', 'N/A')})" for a in ad_groups]))

            elif name == "create_ad_group":
                resp = create_ad_group(args["ad_account_id"], args["campaign_id"], args["name"],
                                       args.get("country", "US"), args.get("daily_budget_micro"),
                                       args.get("bid_micro"), args.get("billing_event", "IMPRESSION"),
                                       args.get("status", "PAUSED"), args.get("age_buckets"),
                                       args.get("genders"), args.get("locales"), args.get("interest_ids"),
                                       args.get("audience_include"), args.get("audience_exclude"),
                                       args.get("placement_group"), args.get("auto_targeting", True),
                                       args.get("targeting_spec"))
                data, exc = extract_created(resp)
                if exc:
                    return err(f"Ad group creation returned exceptions: {json.dumps(exc)}")
                return ok(f"Ad group created (PAUSED unless set otherwise)! ID: {data.get('id')} (status: {data.get('status', 'N/A')})")

            elif name == "update_ad_group":
                resp = update_ad_group(args["ad_account_id"], args["ad_group_id"], args.get("status"),
                                       args.get("name"), args.get("daily_budget_micro"), args.get("bid_micro"),
                                       args.get("auto_targeting"), args.get("placement_group"),
                                       args.get("targeting_spec"))
                data, exc = extract_created(resp)
                if exc:
                    return err(f"Ad group update returned exceptions: {json.dumps(exc)}")
                d = data or {}
                return ok(f"Ad group updated. ID: {d.get('id', args['ad_group_id'])}, Status: {d.get('status', 'N/A')}")

            elif name == "create_ad":
                resp = create_ad(args["ad_account_id"], args["ad_group_id"], args["pin_id"],
                                 args["name"], args.get("status", "PAUSED"), args.get("creative_type", "REGULAR"))
                data, exc = extract_created(resp)
                if exc:
                    return err(f"Ad creation returned exceptions: {json.dumps(exc)}")
                return ok(f"Ad created (PAUSED unless set otherwise)! ID: {data.get('id')} (status: {data.get('status', 'N/A')})")

            elif name == "update_ad":
                resp = update_ad(args["ad_account_id"], args["ad_id"], args.get("status"), args.get("name"))
                data, exc = extract_created(resp)
                if exc:
                    return err(f"Ad update returned exceptions: {json.dumps(exc)}")
                d = data or {}
                return ok(f"Ad updated. ID: {d.get('id', args['ad_id'])}, Status: {d.get('status', 'N/A')}")

            elif name == "create_keywords":
                resp = create_keywords(args["ad_account_id"], args["ad_group_id"], args["values"],
                                       args.get("match_type", "BROAD"), args.get("bid_micro"))
                created = resp.get("keywords", resp) if isinstance(resp, dict) else resp
                errors = resp.get("errors") if isinstance(resp, dict) else None
                msg = f"Keywords submitted: {len(args['values'])}."
                if errors:
                    msg += f" Some errors: {json.dumps(errors)[:300]}"
                return ok(msg)

            elif name == "create_customer_list":
                resp = create_customer_list(args["ad_account_id"], args["name"], args["emails"],
                                            args.get("list_type", "EMAIL"))
                cl = resp.get("items", [resp])[0] if isinstance(resp, dict) and "items" in resp else resp
                cid = cl.get("id") if isinstance(cl, dict) else None
                return ok(f"Customer list created. ID: {cid}. A backing audience is created from it "
                          f"(check list_audiences for the audience ID to use as a seed/exclusion).")

            elif name == "update_customer_list":
                resp = update_customer_list(args["ad_account_id"], args["customer_list_id"],
                                            args["emails"], args.get("operation_type", "ADD"))
                return ok(f"Customer list {args['customer_list_id']} updated "
                          f"({args.get('operation_type', 'ADD')} {len(args['emails'])} records).")

            elif name == "list_audiences":
                auds = list_audiences(args["ad_account_id"])
                if not auds:
                    return ok("No audiences found.")
                return ok("\n".join([f"- {a.get('name', 'Unnamed')} (ID: {a.get('id')}, Type: {a.get('audience_type', 'N/A')}, Size: {a.get('size', 'N/A')})" for a in auds]))

            elif name == "create_actalike_audience":
                resp = create_actalike_audience(args["ad_account_id"], args["name"], args["seed_audience_id"],
                                                args.get("country", "US"), args.get("percentage", 5))
                aid = resp.get("id") if isinstance(resp, dict) else None
                return ok(f"Actalike audience created. ID: {aid} (seeded from {args['seed_audience_id']}).")

            elif name == "get_conversion_tags":
                tags = get_conversion_tags(args["ad_account_id"])
                if not tags:
                    return ok("No conversion tags found.")
                return ok("\n".join([f"- {t.get('name', 'Unnamed')} (ID: {t.get('id')}, Status: {t.get('status', 'N/A')})" for t in tags]))

            elif name == "send_conversion_event":
                resp = send_conversion_event(args["ad_account_id"], args["event_name"], args.get("email"),
                                             args.get("event_id"), args.get("event_time"),
                                             args.get("action_source", "web"), args.get("custom_data"))
                num = resp.get("num_events_received") if isinstance(resp, dict) else None
                return ok(f"Conversion event '{args['event_name']}' sent. "
                          f"Events received: {num if num is not None else json.dumps(resp)[:200]}")

            elif name == "get_campaign_analytics":
                data = get_campaign_analytics(args["ad_account_id"], args["start_date"], args["end_date"], args.get("campaign_ids"))
                return _fmt_analytics("Campaign Analytics Report", data)

            elif name == "get_ad_group_analytics":
                data = get_ad_group_analytics(args["ad_account_id"], args["start_date"], args["end_date"], args.get("ad_group_ids"))
                return _fmt_analytics("Ad Group Analytics Report", data)

            elif name == "get_ad_analytics":
                data = get_ad_analytics(args["ad_account_id"], args["start_date"], args["end_date"], args.get("ad_ids"))
                return _fmt_analytics("Ad Analytics Report", data)

            else:
                return err(f"Unknown tool: {name}")

        except httpx.HTTPStatusError as e:
            return err(f"Pinterest API error: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            return err(f"Error: {str(e)}")

    return err(f"Unknown method: {method}")


def ok(text):
    return {"content": [{"type": "text", "text": text}]}


def err(msg):
    return {"content": [{"type": "text", "text": msg}], "isError": True}


# ----------------------------------------------------------------------------
# HTTP handler: /mcp (POST), /health, /login, /callback, / (GET)
# ----------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _html(self, code, body):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
            return

        if path == "/":
            self._html(200, "<h2>Pinterest MCP (v3)</h2><p>Status: ok. POST /mcp for tools. Visit <a href='/login'>/login</a> to (re)authorize.</p>")
            return

        if path == "/login":
            host = self.headers.get("Host", "")
            redirect_uri = f"https://{host}/callback"
            url = (f"{AUTHORIZE_URL}?client_id={CLIENT_ID}&redirect_uri={redirect_uri}"
                   f"&response_type=code&scope={SCOPES}&state=melia")
            self.send_response(302)
            self.send_header("Location", url)
            self.end_headers()
            return

        if path == "/callback":
            global REFRESH_TOKEN
            qs = parse_qs(parsed.query)
            code = (qs.get("code") or [""])[0]
            if not code:
                self._html(400, "<h3>No code in callback.</h3>")
                return
            host = self.headers.get("Host", "")
            redirect_uri = f"https://{host}/callback"
            try:
                r = httpx.post(
                    f"{PINTEREST_API}/oauth/token",
                    headers={"Authorization": _basic_auth_header(),
                             "Content-Type": "application/x-www-form-urlencoded"},
                    data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
                    timeout=30,
                )
                r.raise_for_status()
                j = r.json()
                rt = j.get("refresh_token", "")
                at = j.get("access_token", "")
                # Make the server usable immediately (until restart).
                if rt:
                    REFRESH_TOKEN = rt
                if at:
                    _token_cache["access_token"] = at
                    _token_cache["expires_at"] = time.time() + int(j.get("expires_in", 2592000))
                self._html(200,
                    "<h2>Authorized.</h2>"
                    "<p>Copy this refresh token and paste it into the host's env var "
                    "<b>PINTEREST_REFRESH_TOKEN</b>, then redeploy so it survives restarts:</p>"
                    f"<textarea style='width:100%;height:120px'>{rt}</textarea>"
                    "<p>Scopes granted: " + j.get("scope", "") + "</p>")
            except httpx.HTTPStatusError as e:
                self._html(502, f"<h3>Token exchange failed: {e.response.status_code}</h3><pre>{e.response.text}</pre>")
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if self.path != "/mcp":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            req = json.loads(body)
            method = req.get("method", "")
            params = req.get("params", {})
            req_id = req.get("id")

            if method == "initialize":
                result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "pinterest-mcp", "version": "3.0.0"}}
            elif method == "notifications/initialized":
                self.send_response(204)
                self.end_headers()
                return
            else:
                result = handle_mcp(method, params)

            response = {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as e:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(e)}}

        out = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


if __name__ == "__main__":
    print(f"Starting Pinterest MCP server (v3) on port {PORT}")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
