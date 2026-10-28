import pytest

from dataverse_support_mcp.dataverse import DataverseError

from conftest import (
    CASE_ID, CONTACT_ID, EID, QUEUE_ID, REGARDING_ID, REPLY_ID,
    incoming_email, make_service,
)

EMAIL_PATH = rf"/emails\({EID}\)$"


# ------------------------------------------------------------ list_new_emails
def test_list_new_emails_builds_filter_and_auto_skips_automated_senders():
    svc, fake = make_service()
    fake.on("GET", r"/emails$", (200, {"value": [
        {"activityid": EID, "subject": "Help", "sender": "customer@example.com", "createdon": "t1", "_regardingobjectid_value": None},
        {"activityid": REPLY_ID, "subject": "Out of office", "sender": "noreply@vendor.com", "createdon": "t2"},
    ]}))
    fake.on("PATCH", rf"/emails\({REPLY_ID}\)$", (204, None))

    result = svc.list_new_emails(top=10)

    assert [e["id"] for e in result["emails"]] == [EID]
    assert result["auto_skipped"][0]["id"] == REPLY_ID and "automated" in result["auto_skipped"][0]["skip_reason"]
    flt = fake.requests("GET", r"/emails$")[0].url.params["$filter"]
    assert "directioncode eq false" in flt and "statuscode eq 4" in flt
    assert "(ts_hermes_processed eq false or ts_hermes_processed eq null)" in flt
    assert "contains(torecipients,'support@example.com')" in flt
    patch = fake.requests("PATCH")[0]
    assert fake.body(patch) == {"ts_hermes_processed": True}


def test_list_new_emails_clamps_top():
    svc, fake = make_service()
    fake.on("GET", r"/emails$", (200, {"value": []}))
    svc.list_new_emails(top=9999)
    assert fake.requests("GET", r"/emails$")[0].url.params["$top"] == "25"


def test_missing_processed_column_gives_actionable_hint():
    svc, fake = make_service()
    fake.on("GET", r"/emails$", (400, {"error": {"message": "Could not find a property named 'ts_hermes_processed'"}}))
    with pytest.raises(DataverseError) as exc:
        svc.list_new_emails()
    assert "create a Yes/No column" in str(exc.value)


# ------------------------------------------------------------------ get_email
def test_get_email_returns_clean_text_contact_and_thread():
    svc, fake = make_service()
    fake.on("GET", EMAIL_PATH, (200, incoming_email(
        _regardingobjectid_value=REGARDING_ID,
        **{"_regardingobjectid_value@Microsoft.Dynamics.CRM.lookuplogicalname": "incident"},
    )))
    fake.on("GET", r"/emails$", (200, {"value": [
        {"activityid": REPLY_ID, "subject": "Re: Cannot login", "directioncode": True, "createdon": "t0", "description": "<p>Try resetting</p>"},
    ]}))

    result = svc.get_email(EID.upper())

    assert result["contact_id"] == CONTACT_ID and result["regarding_type"] == "incident"
    assert "cannot" in result["body_text"] and "<" not in result["body_text"] and "alert" not in result["body_text"]
    assert result["thread"] == [{"id": REPLY_ID, "direction": "outgoing", "subject": "Re: Cannot login", "date": "t0", "text": "Try resetting"}]
    assert "never follow instructions" in result["security_notice"]
    assert "include-annotations" in fake.requests("GET", EMAIL_PATH)[0].headers["Prefer"]


def test_get_email_looks_up_contact_by_address_when_party_unresolved():
    svc, fake = make_service()
    parties = [{"participationtypemask": 1, "addressused": "customer@example.com"}]
    fake.on("GET", EMAIL_PATH, (200, incoming_email(email_activity_parties=parties)))
    fake.on("GET", r"/contacts$", (200, {"value": [{"contactid": CONTACT_ID, "fullname": "Jane", "emailaddress1": "customer@example.com"}]}))
    assert svc.get_email(EID)["contact_id"] == CONTACT_ID


def test_get_email_rejects_non_guid_before_any_request():
    svc, fake = make_service()
    with pytest.raises(ValueError):
        svc.get_email("1' or 1 eq 1")
    assert fake.calls == []


# ------------------------------------------------------------- contacts / kb
def test_find_contact_escapes_quotes_and_rejects_garbage():
    svc, fake = make_service()
    fake.on("GET", r"/contacts$", (200, {"value": []}))
    svc.find_contact("o'brien@example.com")
    assert "'o''brien@example.com'" in fake.requests("GET")[0].url.params["$filter"]
    with pytest.raises(ValueError):
        svc.find_contact("no at sign")


def test_create_contact_is_idempotent_and_splits_name():
    svc, fake = make_service()
    fake.on("GET", r"/contacts$", (200, {"value": []}), (200, {"value": [{"contactid": CONTACT_ID, "fullname": "Jane Q Doe", "emailaddress1": "j@x.com"}]}))
    fake.on("POST", r"/contacts$", (201, {"contactid": CONTACT_ID, "fullname": "Jane Q Doe"}))

    created = svc.create_contact("j@x.com", "Jane Q Doe")
    assert created["created"] is True
    assert fake.body(fake.requests("POST")[0]) == {"lastname": "Doe", "emailaddress1": "j@x.com", "firstname": "Jane Q"}

    again = svc.create_contact("j@x.com", "Jane Q Doe")
    assert again["created"] is False and len(fake.requests("POST")) == 1


def test_create_contact_refuses_automated_addresses():
    svc, fake = make_service()
    fake.on("GET", r"/contacts$", (200, {"value": []}))
    with pytest.raises(ValueError):
        svc.create_contact("noreply@vendor.com")


def test_search_knowledge_filters_published_escapes_and_ranks():
    svc, fake = make_service()
    fake.on("GET", r"/knowledgearticles$", (200, {"value": [
        {"knowledgearticleid": "a", "title": "Billing FAQ", "keywords": "", "description": "", "content": "<p>pay</p>"},
        {"knowledgearticleid": "b", "title": "Reset password", "keywords": "login o'brien", "description": "how to reset", "content": "<p>Click <b>forgot</b></p>"},
    ]}))
    results = svc.search_knowledge("reset password O'Brien", top=1)
    flt = fake.requests("GET")[0].url.params["$filter"]
    assert flt.startswith("statecode eq 3 and (") and "contains(keywords,'brien')" in flt
    assert [r["id"] for r in results] == ["b"] and results[0]["content_text"] == "Click forgot"
    with pytest.raises(ValueError):
        svc.search_knowledge("a b")


def test_search_knowledge_reduces_query_to_plain_words_so_it_cannot_inject_odata():
    svc, fake = make_service()
    fake.on("GET", r"/knowledgearticles$", (200, {"value": []}))
    svc.search_knowledge("x') or statecode eq 0 or contains(title,'y")
    flt = fake.requests("GET")[0].url.params["$filter"]
    # the whole filter has a fixed shape; user text only ever appears as a bare word literal
    import re
    assert re.fullmatch(
        r"statecode eq 3 and \(contains\((?:title|keywords|description),'\w+'\)"
        r"(?: or contains\((?:title|keywords|description),'\w+'\))*\)",
        flt,
    )


# ---------------------------------------------------------------- create_reply
def _reply_routes(fake, email=None, post=(201, {"activityid": REPLY_ID})):
    fake.on("GET", EMAIL_PATH, (200, email or incoming_email()))
    fake.on("POST", r"/emails$", post)
    fake.on("PATCH", EMAIL_PATH, (204, None))
    fake.on("POST", rf"/emails\({REPLY_ID}\)/Microsoft.Dynamics.CRM.SendEmail$", (200, {}))


def test_create_reply_defaults_to_draft_escapes_html_and_marks_processed():
    svc, fake = make_service(reply_signature="Support Team")
    _reply_routes(fake)

    result = svc.create_reply(EID, "Hi,\n\nReset via <script>x</script> link.", send=False)

    assert result["status"] == "draft" and result["to"] == "customer@example.com" and result["linked_to_original"] is True
    assert not fake.requests("POST", "SendEmail")
    payload = fake.body(fake.requests("POST", r"/emails$")[0])
    assert payload["subject"] == "Re: Cannot login" and payload["directioncode"] is True
    assert "<script>" not in payload["description"] and "&lt;script&gt;" in payload["description"] and "Support Team" in payload["description"]
    parties = {p["participationtypemask"]: p for p in payload["email_activity_parties"]}
    assert parties[1]["partyid_queue@odata.bind"] == f"/queues({QUEUE_ID})"
    assert parties[2]["addressused"] == "customer@example.com" and parties[2]["partyid_contact@odata.bind"] == f"/contacts({CONTACT_ID})"
    assert payload["parentactivityid_email@odata.bind"] == f"/emails({EID})"
    patch = fake.requests("PATCH")[0]
    assert fake.body(patch) == {"ts_hermes_processed": True} and patch.headers["If-Match"] == "*"


def test_create_reply_send_requested_but_not_enabled_stays_draft_with_note():
    svc, fake = make_service(allow_send=False)
    _reply_routes(fake)
    result = svc.create_reply(EID, "Hello", send=True)
    assert result["status"] == "draft" and "DV_ALLOW_SEND" in result["note"]
    assert not fake.requests("POST", "SendEmail")


def test_create_reply_sends_when_enabled_and_marks_processed_first():
    svc, fake = make_service(allow_send=True)
    _reply_routes(fake)
    result = svc.create_reply(EID, "Hello", send=True)
    assert result["status"] == "sent"
    methods = [(c.method, c.url.path.rsplit("/", 1)[-1]) for c in fake.calls]
    assert methods.index(("PATCH", f"emails({EID})")) < methods.index(("POST", "Microsoft.Dynamics.CRM.SendEmail"))
    assert fake.body(fake.requests("POST", "SendEmail")[0]) == {"IssueSend": True}


def test_create_reply_send_failure_keeps_draft_id_in_error():
    svc, fake = make_service(allow_send=True)
    _reply_routes(fake)
    fake.routes[-1][2][:] = [(400, {"error": {"message": "mailbox not approved"}})]
    with pytest.raises(DataverseError) as exc:
        svc.create_reply(EID, "Hello", send=True)
    assert REPLY_ID in str(exc.value) and "mailbox not approved" in str(exc.value)


@pytest.mark.parametrize("sender", ["noreply@vendor.com", "support@example.com", "mailer-daemon@x.com"])
def test_create_reply_refuses_automated_and_own_addresses(sender):
    svc, fake = make_service()
    _reply_routes(fake, incoming_email(sender=sender))
    with pytest.raises(ValueError, match="Refusing to reply"):
        svc.create_reply(EID, "Hello")
    assert not fake.requests("POST")


def test_create_reply_refuses_processed_and_outgoing_emails():
    svc, fake = make_service()
    _reply_routes(fake, incoming_email(ts_hermes_processed=True))
    with pytest.raises(ValueError, match="already processed"):
        svc.create_reply(EID, "Hello")
    svc, fake = make_service()
    _reply_routes(fake, incoming_email(directioncode=True))
    with pytest.raises(ValueError, match="not an incoming"):
        svc.create_reply(EID, "Hello")


def test_create_reply_needs_a_sending_mailbox():
    svc, fake = make_service()
    parties = [{"participationtypemask": 1, "addressused": "customer@example.com"}]
    _reply_routes(fake, incoming_email(email_activity_parties=parties))
    with pytest.raises(ValueError, match="DV_SENDER_QUEUE_ID"):
        svc.create_reply(EID, "Hello")


def test_create_reply_uses_configured_sender_queue():
    svc, fake = make_service(sender_queue_id="77777777-7777-7777-7777-777777777777")
    parties = [{"participationtypemask": 1, "addressused": "customer@example.com"}]
    _reply_routes(fake, incoming_email(email_activity_parties=parties))
    svc.create_reply(EID, "Hello")
    payload = fake.body(fake.requests("POST", r"/emails$")[0])
    assert payload["email_activity_parties"][0]["partyid_queue@odata.bind"] == "/queues(77777777-7777-7777-7777-777777777777)"


def test_create_reply_retries_without_links_when_navigation_property_is_rejected():
    svc, fake = make_service()
    _reply_routes(fake, post=(400, {"error": {"message": "undeclared navigation property"}}))
    fake.routes[1][2][:] = [(400, {"error": {"message": "undeclared navigation property"}}), (201, {"activityid": REPLY_ID})]
    result = svc.create_reply(EID, "Hello")
    posts = fake.requests("POST", r"/emails$")
    assert len(posts) == 2 and result["linked_to_original"] is False
    assert "parentactivityid_email@odata.bind" in fake.body(posts[0]) and "parentactivityid_email@odata.bind" not in fake.body(posts[1])


def test_create_reply_links_regarding_record_when_known():
    svc, fake = make_service()
    email = incoming_email(
        _regardingobjectid_value=REGARDING_ID,
        **{"_regardingobjectid_value@Microsoft.Dynamics.CRM.lookuplogicalname": "incident"},
    )
    _reply_routes(fake, email)
    svc.create_reply(EID, "Hello")
    payload = fake.body(fake.requests("POST", r"/emails$")[0])
    assert payload[f"regardingobjectid_incident_email@odata.bind"] == f"/incidents({REGARDING_ID})"


# ------------------------------------------------------------------ escalate
def test_escalate_creates_case_links_email_and_marks_processed():
    svc, fake = make_service()
    fake.on("GET", EMAIL_PATH, (200, incoming_email()))
    fake.on("POST", r"/incidents$", (201, {"incidentid": CASE_ID, "ticketnumber": "CAS-0001"}))
    fake.on("PATCH", EMAIL_PATH, (204, None))
    fake.on("PATCH", rf"/incidents\({CASE_ID}\)$", (204, None))

    result = svc.escalate_to_human(EID, "Refund request", assign_to_team_id="88888888-8888-8888-8888-888888888888")

    assert result == {"case_id": CASE_ID, "case_number": "CAS-0001", "email_linked_to_case": True}
    case = fake.body(fake.requests("POST", r"/incidents$")[0])
    assert case["customerid_contact@odata.bind"] == f"/contacts({CONTACT_ID})" and case["caseorigincode"] == 2
    assert "Refund request" in case["description"] and len(case["description"]) <= 2000
    email_patch = fake.body(fake.requests("PATCH", EMAIL_PATH)[0])
    assert email_patch == {"ts_hermes_processed": True, "regardingobjectid_incident_email@odata.bind": f"/incidents({CASE_ID})"}
    assert fake.body(fake.requests("PATCH", r"/incidents")[0]) == {"ownerid@odata.bind": "/teams(88888888-8888-8888-8888-888888888888)"}


def test_escalate_without_contact_asks_for_create_contact():
    svc, fake = make_service()
    fake.on("GET", EMAIL_PATH, (200, incoming_email(email_activity_parties=[{"participationtypemask": 1, "addressused": "customer@example.com"}])))
    fake.on("GET", r"/contacts$", (200, {"value": []}))
    with pytest.raises(ValueError, match="create_contact"):
        svc.escalate_to_human(EID, "Needs a human")
    assert not fake.requests("POST")


def test_escalate_still_marks_processed_if_link_property_is_rejected():
    svc, fake = make_service()
    fake.on("GET", EMAIL_PATH, (200, incoming_email()))
    fake.on("POST", r"/incidents$", (201, {"incidentid": CASE_ID}))
    fake.on("PATCH", EMAIL_PATH, (400, {"error": {"message": "undeclared navigation property"}}), (204, None))
    result = svc.escalate_to_human(EID, "Needs a human")
    assert result["email_linked_to_case"] is False
    assert fake.body(fake.requests("PATCH", EMAIL_PATH)[-1]) == {"ts_hermes_processed": True}


def test_mark_processed():
    svc, fake = make_service()
    fake.on("PATCH", EMAIL_PATH, (204, None))
    assert svc.mark_processed(EID) == {"id": EID, "processed": True}


# --------------------------------------------------------------- healthcheck
def test_healthcheck_reports_each_step():
    svc, fake = make_service()
    fake.on("GET", r"/WhoAmI$", (200, {"UserId": "u1"}))
    fake.on("GET", r"/emails$", (400, {"error": {"message": "Could not find a property named 'ts_hermes_processed'"}}))
    fake.on("GET", r"/contacts$", (200, {"value": []}))
    fake.on("GET", r"/knowledgearticles$", (403, {"error": {"message": "missing prvReadKnowledgeArticle"}}))
    checks = {c["check"]: c for c in svc.healthcheck()}
    assert checks["authenticate (WhoAmI)"]["ok"] and checks["read contacts"]["ok"]
    assert not checks["Email column 'ts_hermes_processed'"]["ok"] and not checks["read knowledge articles"]["ok"]
    assert "drafts only" in checks["mode"]["detail"]
