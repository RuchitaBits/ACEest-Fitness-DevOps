from tests.test_clients import add_client


def test_program_list_and_generation(logged_in):
    assert b"Fat Loss" in logged_in.get("/programs").data
    response = logged_in.post("/programs/generate", data={"program": "Muscle Gain", "experience": "Advanced"})
    assert response.status_code == 200
    assert b"Deadlift" in response.data


def test_workout_creation(logged_in):
    add_client(logged_in)
    with logged_in.application.app_context():
        from app import query_db
        client_id = query_db("SELECT id FROM clients", one=True)["id"]
    response = logged_in.post(
        "/workouts/add",
        data={
            "client_id": client_id,
            "workout_type": "Strength",
            "duration": 45,
            "notes": "Good session",
            "exercise_name": "Squat",
            "sets": "3",
            "reps": "10",
            "exercise_weight": "40",
        },
    )
    assert response.status_code == 302
    assert b"Good session" not in logged_in.get("/workouts").data
    assert b"Sam Lee" in logged_in.get("/workouts").data
    assert b"Squat" in logged_in.get("/workouts").data
    with logged_in.application.app_context():
        from app import query_db

        exercise = query_db("SELECT * FROM exercises", one=True)
        assert exercise["name"] == "Squat"
        assert exercise["sets"] == 3
        assert exercise["reps"] == 10
        assert exercise["weight"] == 40


def test_invalid_exercise_does_not_create_partial_workout(logged_in):
    add_client(logged_in)
    with logged_in.application.app_context():
        from app import query_db

        client_id = query_db("SELECT id FROM clients", one=True)["id"]

    response = logged_in.post(
        "/workouts/add",
        data={
            "client_id": client_id,
            "workout_type": "Strength",
            "duration": 45,
            "exercise_name": "Squat",
        },
    )

    assert response.status_code == 302
    with logged_in.application.app_context():
        from app import query_db

        assert query_db(
            "SELECT COUNT(*) AS count FROM workouts", one=True
        )["count"] == 0


def test_progress_validation(logged_in):
    add_client(logged_in)
    with logged_in.application.app_context():
        from app import query_db
        client_id = query_db("SELECT id FROM clients", one=True)["id"]
    logged_in.post(f"/clients/{client_id}/progress", data={"adherence": 130})
    assert b"No progress records" in logged_in.get(f"/clients/{client_id}").data