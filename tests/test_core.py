import httpx
import pytest

from dataverse_support_mcp.config import ConfigError, Settings
from dataverse_support_mcp.dataverse import DataverseClient, DataverseError
from dataverse_support_mcp.text import html_to_text, text_to_html
from dataverse_support_mcp.util import odata_quote, require_guid

from conftest import ORG, make_settings


# ---------------------------------------------------------------- util / text
def test_require_guid_accepts_and_normalises():
    assert require_guid("{ABCDEFAB-1111-2222-3333-444444444444}") == "abcdefab-1111-2222-3333-444444444444"


@pytest.mark.parametrize("bad", ["", None, "123", "1111' or 1 eq 1 --", "11111111-1111-1111-1111-11111111111g"])
def test_require_guid_rejects(bad):
    with pytest.raises(ValueError):
        require_guid(bad)


def test_odata_quote_doubles_single_quotes():
    assert odata_quote("o'brien") == "'o''brien'"


def test_html_to_text_strips_markup_scripts_and_styles():
    text = html_to_text("<head><style>p{}</style></head><p>Hi&nbsp;there</p><script>x()</script><ul><li>a</li><li>b</li></ul>")
    assert "Hi there" in text and "- a" in text and "- b" in text
    assert "x()" not in text and "p{}" not in text and "<" not in text


def test_html_to_text_truncates():
    assert html_to_text("<p>" + "a" * 100 + "</p>", max_chars=10).endswith("[... truncated]")


def test_text_to_html_escapes_everything():
    out = text_to_html("Hello <script>alert(1)</script>\nline two\n\nSecond & last", signature="Team")
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert out.count("<p>") == 3 and "<br>" in out and "Team" in out


# --------------------------------------------------------------------- config
def test_settings_from_env_requires_core_vars():
    with pytest.raises(ConfigError) as exc:
        Settings.from_env({"DV_URL": ORG})
    assert "DV_TENANT_ID" in str(exc.value) and "DV_CLIENT_SECRET" in str(exc.value)


def test_settings_from_env_parses_options():
    s = Settings.from_env(
        {
            "DV_URL": ORG + "/",
            "DV_TENANT_ID": "t",
            "DV_CLIENT_ID": "c",
            "DV_CLIENT_SECRET": "s",
            "DV_ALLOW_SEND": "TRUE",
            "DV_SUPPORT_ADDRESS": "Support@Example.com",
            "DV_REPLY_SIGNATURE": "Best,\\nSupport",
            "DV_BLOCKED_SENDERS": "@spam.io, bad@x.com",
            "DV_SENDER_QUEUE_ID": "44444444-4444-4444-4444-444444444444",
        }
    )
    assert s.org_url == ORG and s.allow_send and s.support_address == "support@example.com"
    assert s.reply_signature == "Best,\nSupport" and s.blocked_senders == ("@spam.io", "bad@x.com")
    assert s.api_base == ORG + "/api/data/v9.2" and s.scope == ORG + "/.default"


def test_settings_default_is_draft_only_and_https_required():
    base = {"DV_URL": ORG, "DV_TENANT_ID": "t", "DV_CLIENT_ID": "c", "DV_CLIENT_SECRET": "s"}
    assert Settings.from_env(base).allow_send is False
    with pytest.raises(ConfigError):
        Settings.from_env({**base, "DV_URL": "http://insecure.example"})
    with pytest.raises(ConfigError):
        Settings.from_env({**base, "DV_SENDER_QUEUE_ID": "not-a-guid"})


@pytest.mark.parametrize(
    "sender",
    ["noreply@x.com", "No-Reply@x.com", "do.not.reply@x.com", "MAILER-DAEMON@x.com", "postmaster@x.com",
     "bounce+abc@x.com", "Support <support@example.com>", "", "not-an-address", "x@spam.io", "bad@x.com"],
)
def test_blocked_reason_blocks(sender):
    s = make_settings(blocked_senders=("@spam.io", "bad@x.com"))
    assert s.blocked_reason(sender)


def test_blocked_reason_allows_normal_customers():
    assert make_settings().blocked_reason("Jane Doe <jane@customer.com>") is None


# ------------------------------------------------------------- dataverse client
def _client(handler, sleeps=None):
    return DataverseClient(
        make_settings(),
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        token_provider=lambda: "tok",
        sleep=(sleeps.append if sleeps is not None else (lambda _s: None)),
    )


def test_client_encodes_spaces_as_percent20_and_sends_bearer():
    seen = {}

    def handler(request):
        seen["raw"] = request.url.raw_path.decode()
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json={"value": []})

    _client(handler).get("emails", params={"$filter": "a eq 1 and b eq 2", "$top": 5})
    assert "%20" in seen["raw"] and "+" not in seen["raw"] and "$top=5" in seen["raw"]
    assert seen["auth"] == "Bearer tok"


def test_client_retries_429_then_succeeds():
    attempts, sleeps = [], []

    def handler(request):
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": {"message": "slow down"}})
        return httpx.Response(200, json={"ok": True})

    assert _client(handler, sleeps).get("emails") == {"ok": True}
    assert len(attempts) == 3 and sleeps == [2.0, 2.0]


def test_client_maps_errors_to_dataverse_error():
    def handler(request):
        return httpx.Response(403, json={"error": {"message": "Principal user is missing prvReadEmail"}})

    with pytest.raises(DataverseError) as exc:
        _client(handler).get("emails")
    assert exc.value.status == 403 and "prvReadEmail" in str(exc.value)


def test_patch_sends_if_match_star_and_handles_204():
    seen = {}

    def handler(request):
        seen["if_match"] = request.headers.get("If-Match")
        return httpx.Response(204)

    assert _client(handler).patch("emails(x)", {"a": 1}) == {}
    assert seen["if_match"] == "*"
