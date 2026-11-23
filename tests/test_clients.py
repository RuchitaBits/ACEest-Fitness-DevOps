def add_client(client, name="Sam Lee"):
    return client.post("/clients/add", data={"name": name, "age": 30, "height": 175, "weight": 70, "target_weight": 68, "program": "Fat Loss"})


def test_client_creation_retrieval_update_delete(logged_in):
    response = add_client(logged_in)
    assert response.status_code == 302
    page = logged_in.get("/clients")
    assert b"Sam Lee" in page.data
    with logged_in.application.app_context():
        from app import query_db
        client_id = query_db("SELECT id FROM clients", one=True)["id"]
    detail = logged_in.get(f"/clients/{client_id}")
    assert detail.status_code == 200
    logged_in.post(f"/clients/{client_id}/update", data={"weight": 68, "program": "Muscle Gain", "membership_status": "Active"})
    assert b"2380" in logged_in.get(f"/clients/{client_id}").data
    logged_in.post(f"/clients/{client_id}/delete")
    assert b"Sam Lee" not in logged_in.get("/clients").data


def test_invalid_client_input(logged_in):
    add_client(logged_in, name="")
    assert b"No clients yet" in logged_in.get("/clients").data


def test_non_finite_client_values_are_rejected(logged_in):
    for field in ("age", "height", "weight", "target_weight", "target_adherence"):
        data = {
            "name": "Invalid Client",
            "age": "30",
            "height": "175",
            "weight": "70",
            "target_weight": "68",
            "target_adherence": "80",
            "program": "Fat Loss",
        }
        data[field] = "nan"
        response = logged_in.post("/clients/add", data=data)
        assert response.status_code == 302

    with logged_in.application.app_context():
        from app import query_db

        assert query_db("SELECT COUNT(*) AS count FROM clients", one=True)["count"] == 0


def test_non_finite_client_update_is_rejected(logged_in):
    add_client(logged_in)
    with logged_in.application.app_context():
        from app import query_db

        client_id = query_db("SELECT id FROM clients", one=True)["id"]

    response = logged_in.post(
        f"/clients/{client_id}/update",
        data={"weight": "inf", "program": "Muscle Gain"},
    )

    assert response.status_code == 302
    with logged_in.application.app_context():
        from app import query_db

        client = query_db(
            "SELECT weight, program FROM clients WHERE id = ?",
            (client_id,),
            one=True,
        )
        assert client["weight"] == 70
        assert client["program"] == "Fat Loss"


def test_body_metrics_are_recorded_and_shown_in_client_history(logged_in):
    add_client(logged_in)
    with logged_in.application.app_context():
        from app import query_db

        client_id = query_db("SELECT id FROM clients", one=True)["id"]

    response = logged_in.post(
        f"/clients/{client_id}/metrics",
        data={
            "metric_date": "2026-03-12",
            "weight": "68.5",
            "waist": "82",
            "body_fat": "21.4",
        },
    )
    logged_in.post(
        f"/clients/{client_id}/progress",
        data={"week_date": "2026-03-12", "adherence": "80"},
    )

    assert response.status_code == 302
    page = logged_in.get(f"/clients/{client_id}")
    assert b"Body metrics" in page.data
    assert b"2026-03-12" in page.data
    assert b"68.5" in page.data
    assert b"82.0" in page.data
    assert b"Body weight trend" in page.data
    assert b"Weekly adherence trend" in page.data
    assert b"BMI estimate" in page.data
    assert page.data.count(b"<circle") == 2
    with logged_in.application.app_context():
        assert query_db(
            "SELECT COUNT(*) AS count FROM metrics WHERE client_id = ?",
            (client_id,),
            one=True,
        )["count"] == 1


def test_body_metrics_reject_empty_or_out_of_range_values(logged_in):
    add_client(logged_in)
    with logged_in.application.app_context():
        from app import query_db

        client_id = query_db("SELECT id FROM clients", one=True)["id"]

    for data in (
        {"metric_date": "2026-03-12"},
        {"metric_date": "2026-03-12", "body_fat": "101"},
        {"metric_date": "not-a-date", "weight": "70"},
    ):
        response = logged_in.post(f"/clients/{client_id}/metrics", data=data)
        assert response.status_code == 302

    with logged_in.application.app_context():
        assert query_db(
            "SELECT COUNT(*) AS count FROM metrics WHERE client_id = ?",
            (client_id,),
            one=True,
        )["count"] == 0


def test_progress_rejects_invalid_date_and_missing_client(logged_in):
    add_client(logged_in)
    with logged_in.application.app_context():
        from app import query_db

        client_id = query_db("SELECT id FROM clients", one=True)["id"]

    invalid_date = logged_in.post(
        f"/clients/{client_id}/progress",
        data={"week_date": "not-a-date", "adherence": "80"},
    )
    missing_client = logged_in.post(
        f"/clients/{client_id + 1}/progress",
        data={"week_date": "2026-03-12", "adherence": "80"},
    )

    assert invalid_date.status_code == 302
    assert missing_client.status_code == 404
    with logged_in.application.app_context():
        from app import query_db

        assert query_db("SELECT COUNT(*) AS count FROM progress", one=True)["count"] == 0


def test_client_report_download_is_authenticated_and_returns_pdf(logged_in):
    add_client(logged_in, name="Sam & Lee")
    with logged_in.application.app_context():
        from app import query_db

        client_id = query_db("SELECT id FROM clients", one=True)["id"]

    response = logged_in.get(f"/clients/{client_id}/report.pdf")

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF-")


def test_client_report_requires_login(client):
    response = client.get("/clients/1/report.pdf")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]