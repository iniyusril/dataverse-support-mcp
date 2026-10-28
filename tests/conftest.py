import json
import re

import httpx
import pytest

from dataverse_support_mcp.config import Settings
from dataverse_support_mcp.dataverse import DataverseClient
from dataverse_support_mcp.service import SupportService

ORG = "https://org.crm5.dynamics.com"
EID = "11111111-1111-1111-1111-111111111111"
REPLY_ID = "22222222-2222-2222-2222-222222222222"
CONTACT_ID = "33333333-3333-3333-3333-333333333333"
QUEUE_ID = "44444444-4444-4444-4444-444444444444"
CASE_ID = "55555555-5555-5555-5555-555555555555"
REGARDING_ID = "66666666-6666-6666-6666-666666666666"


class FakeDataverse:
    """Records requests and answers from a list of (method, path regex, responses)."""

    def __init__(self):
        self.routes = []
        self.calls = []

    def on(self, method, pattern, *responses):
        # each response is (status, json_body); the last one repeats
        self.routes.append([method, re.compile(pattern), list(responses)])

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        for method, pattern, responses in self.routes:
            if request.method == method and pattern.search(request.url.path):
                status, body = responses.pop(0) if len(responses) > 1 else responses[0]
                if body is None:
                    return httpx.Response(status)
                return httpx.Response(status, json=body)
        return httpx.Response(404, json={"error": {"message": f"no route for {request.method} {request.url.path}"}})

    def requests(self, method, pattern=""):
        rx = re.compile(pattern)
        return [c for c in self.calls if c.method == method and rx.search(c.url.path)]

    @staticmethod
    def body(request: httpx.Request):
        return json.loads(request.content.decode())


def make_settings(**overrides) -> Settings:
    base = dict(
        org_url=ORG,
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        support_address="support@example.com",
        processed_field="ts_hermes_processed",
        max_body_chars=8000,
    )
    base.update(overrides)
    return Settings(**base)


def make_service(**settings_overrides):
    fake = FakeDataverse()
    settings = make_settings(**settings_overrides)
    client = DataverseClient(
        settings,
        http=httpx.Client(transport=httpx.MockTransport(fake.handler)),
        token_provider=lambda: "test-token",
        sleep=lambda _s: None,
    )
    return SupportService(client, settings), fake


@pytest.fixture
def svc():
    return make_service()


def incoming_email(**overrides):
    email = {
        "activityid": EID,
        "subject": "Cannot login",
        "sender": "customer@example.com",
        "torecipients": "support@example.com",
        "description": "<html><head><style>p{}</style></head><body><p>Hello,</p><p>I <b>cannot</b> login.</p><script>alert(1)</script></body></html>",
        "createdon": "2026-10-28T08:00:00Z",
        "directioncode": False,
        "statuscode": 4,
        "ts_hermes_processed": None,
        "_regardingobjectid_value": None,
        "email_activity_parties": [
            {"participationtypemask": 1, "addressused": "customer@example.com", "_partyid_value": CONTACT_ID, "partyobjecttypecode": 2},
            {"participationtypemask": 2, "addressused": "support@example.com", "_partyid_value": QUEUE_ID, "partyobjecttypecode": 2020},
        ],
    }
    email.update(overrides)
    return email
