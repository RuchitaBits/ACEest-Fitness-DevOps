import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
K8S = ROOT / "k8s"
ASSIGNMENT_K8S = ROOT / "kubernetes"


def load_documents(name):
    with (K8S / name).open(encoding="utf-8") as manifest:
        return [document for document in yaml.safe_load_all(manifest) if document]


def find_document(documents, kind, name):
    return next(
        document
        for document in documents
        if document["kind"] == kind and document["metadata"]["name"] == name
    )


def test_all_kubernetes_manifests_parse_as_yaml():
    files = sorted(K8S.glob("*.yaml"))
    assert files
    for path in files:
        with path.open(encoding="utf-8") as manifest:
            documents = [document for document in yaml.safe_load_all(manifest) if document]
        assert documents, path.name
        assert all("apiVersion" in document and "kind" in document for document in documents)


def test_deployment_and_service_selectors_match_pod_labels():
    documents = [
        document
        for path in K8S.glob("*.yaml")
        for document in load_documents(path.name)
    ]
    deployments = [document for document in documents if document["kind"] == "Deployment"]
    services = [document for document in documents if document["kind"] == "Service"]

    for deployment in deployments:
        selector = deployment["spec"]["selector"]["matchLabels"]
        labels = deployment["spec"]["template"]["metadata"]["labels"]
        assert all(labels.get(key) == value for key, value in selector.items())

    for service in services:
        selector = service["spec"].get("selector", {})
        assert selector, service["metadata"]["name"]
        assert any(
            all(
                deployment["spec"]["template"]["metadata"]["labels"].get(key) == value
                for key, value in selector.items()
            )
            for deployment in deployments
        ), service["metadata"]["name"]


def test_blue_green_service_selectors_match_each_color():
    service = load_documents("service.yaml")[0]
    selector = service["spec"]["selector"].copy()
    selector.pop("app.kubernetes.io/track")
    for color in ("blue", "green"):
        deployment = load_documents(f"deployment-{color}.yaml")[0]
        template_labels = deployment["spec"]["template"]["metadata"]["labels"]
        assert all(template_labels[key] == value for key, value in selector.items())
        assert template_labels["app.kubernetes.io/track"] == color
        assert deployment["spec"]["replicas"] == 0
        assert deployment["spec"]["strategy"]["rollingUpdate"]["maxSurge"] == 0


def test_canary_virtual_service_routes_real_weighted_traffic():
    documents = load_documents("canary.yaml")
    destination_rule = find_document(documents, "DestinationRule", "aceest-canary")
    virtual_service = find_document(documents, "VirtualService", "aceest-canary")
    subset_names = {item["name"] for item in destination_rule["spec"]["subsets"]}
    routes = virtual_service["spec"]["http"][0]["route"]

    assert {route["destination"]["subset"] for route in routes} == subset_names
    assert sum(route["weight"] for route in routes) == 100
    assert sorted(route["weight"] for route in routes) == [10, 90]


def test_shadow_traffic_is_mirrored_to_an_isolated_safe_copy():
    documents = load_documents("shadow.yaml")
    virtual_service = find_document(documents, "VirtualService", "aceest-shadow")
    primary_deployment = find_document(documents, "Deployment", "aceest-shadow-primary")
    shadow_deployment = find_document(documents, "Deployment", "aceest-shadow-copy")
    shadow_service = find_document(documents, "Service", "aceest-shadow-copy")
    request_route = virtual_service["spec"]["http"][0]
    shadow_container = shadow_deployment["spec"]["template"]["spec"]["containers"][0]
    shadow_environment = {item["name"]: item.get("value") for item in shadow_container["env"]}

    assert request_route["mirror"]["host"] == shadow_service["metadata"]["name"]
    assert request_route["mirrorPercentage"]["value"] == 100.0
    assert primary_deployment["spec"]["replicas"] == 0
    assert shadow_deployment["spec"]["replicas"] == 0
    assert shadow_environment["SHADOW_MODE"] == "true"
    assert shadow_deployment["spec"]["template"]["spec"]["volumes"][0]["emptyDir"] == {}


def test_ab_route_uses_beta_header_and_has_a_control_route():
    documents = load_documents("ab-testing.yaml")
    virtual_service = find_document(documents, "VirtualService", "aceest-ab")
    routes = virtual_service["spec"]["http"]
    beta = routes[0]
    control = routes[1]

    assert beta["match"][0]["headers"]["x-aceest-group"]["exact"] == "beta"
    assert beta["route"][0]["destination"]["subset"] == "b"
    assert control["route"][0]["destination"]["subset"] == "a"


def test_rolling_update_and_persistent_storage_are_configured():
    documents = load_documents("rolling-update.yaml")
    deployment = find_document(documents, "Deployment", "aceest-rolling")
    service = find_document(documents, "Service", "aceest-rolling-service")
    pod_spec = deployment["spec"]["template"]["spec"]
    selector = service["spec"]["selector"]
    labels = deployment["spec"]["template"]["metadata"]["labels"]

    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"]["rollingUpdate"] == {
        "maxUnavailable": 1,
        "maxSurge": 0,
    }
    assert deployment["spec"]["revisionHistoryLimit"] > 0
    assert all(labels[key] == value for key, value in selector.items())
    assert any(volume.get("persistentVolumeClaim", {}).get("claimName") == "aceest-data" for volume in pod_spec["volumes"])
    pvc = load_documents("pvc.yaml")[0]
    assert pvc["spec"]["accessModes"] == ["ReadWriteOnce"]
    container = pod_spec["containers"][0]
    assert re.fullmatch(
        r"aceest-fitness:sha-[0-9a-f]{40}", container["image"]
    )
    assert container["startupProbe"]["httpGet"]["path"] == "/health"
    assert container["readinessProbe"]["httpGet"]["path"] == "/health"
    assert container["livenessProbe"]["httpGet"]["path"] == "/health"


def test_assignment_manifests_configure_rolling_flask_deployment_and_service():
    deployment = yaml.safe_load(
        (ASSIGNMENT_K8S / "deployment.yaml").read_text(encoding="utf-8")
    )
    service = yaml.safe_load(
        (ASSIGNMENT_K8S / "service.yaml").read_text(encoding="utf-8")
    )
    labels = deployment["spec"]["template"]["metadata"]["labels"]
    container = deployment["spec"]["template"]["spec"]["containers"][0]

    assert deployment["kind"] == "Deployment"
    assert deployment["spec"]["replicas"] == 0
    assert deployment["spec"]["strategy"]["type"] == "RollingUpdate"
    assert deployment["spec"]["selector"]["matchLabels"] == service["spec"]["selector"]
    assert all(labels[key] == value for key, value in service["spec"]["selector"].items())
    assert service["kind"] == "Service"
    assert re.fullmatch(
        r"aceest-fitness:sha-[0-9a-f]{40}", container["image"]
    )
    assert container["ports"][0]["containerPort"] == 5000
    assert container["readinessProbe"]["httpGet"]["path"] == "/health"
    secret_names = {
        item["name"]
        for item in container["env"]
        if "secretKeyRef" in item.get("valueFrom", {})
    }
    assert {"ADMIN_USERNAME", "ADMIN_PASSWORD", "FLASK_SECRET_KEY"} <= secret_names


def test_deployments_use_full_sha_image_tags_and_secret_references():
    for path in K8S.glob("*.yaml"):
        for document in load_documents(path.name):
            if document["kind"] != "Deployment":
                continue
            for container in document["spec"]["template"]["spec"]["containers"]:
                assert re.fullmatch(
                    r"aceest-fitness:sha-[0-9a-f]{40}", container["image"]
                )
                env_names = {item["name"] for item in container.get("env", [])}
                if document["metadata"]["name"] != "aceest-shadow-copy":
                    assert {"ADMIN_USERNAME", "ADMIN_PASSWORD", "FLASK_SECRET_KEY"} <= env_names


def test_sqlite_deployments_never_roll_out_overlapping_pods():
    documents = [
        document
        for path in K8S.glob("*.yaml")
        for document in load_documents(path.name)
    ]
    sqlite_deployments = []
    for document in documents:
        if document["kind"] != "Deployment":
            continue
        pod_spec = document["spec"]["template"]["spec"]
        if any(
            volume.get("persistentVolumeClaim", {}).get("claimName") == "aceest-data"
            for volume in pod_spec.get("volumes", [])
        ):
            sqlite_deployments.append(document)

    assert sum(deployment["spec"]["replicas"] for deployment in sqlite_deployments) == 1
    assert all(
        deployment["spec"].get("strategy", {})
        .get("rollingUpdate", {})
        .get("maxSurge", 0)
        == 0
        for deployment in sqlite_deployments
    )


def test_canary_and_ab_examples_are_disabled_and_isolated_for_sqlite():
    for manifest_name in ("canary.yaml", "ab-testing.yaml"):
        documents = load_documents(manifest_name)
        deployments = [
            document for document in documents if document["kind"] == "Deployment"
        ]
        assert deployments
        for deployment in deployments:
            pod_spec = deployment["spec"]["template"]["spec"]
            container = pod_spec["containers"][0]
            environment = {item["name"]: item.get("value") for item in container["env"]}
            assert deployment["spec"]["replicas"] == 0
            assert environment["SHADOW_MODE"] == "true"
            assert all(
                "persistentVolumeClaim" not in volume
                for volume in pod_spec.get("volumes", [])
            )
            assert any("emptyDir" in volume for volume in pod_spec["volumes"])


def test_workloads_have_probes_resources_and_configuration():
    workloads = [
        document
        for path in K8S.glob("*.yaml")
        for document in load_documents(path.name)
        if document["kind"] == "Deployment"
    ]
    assignment = yaml.safe_load(
        (ASSIGNMENT_K8S / "deployment.yaml").read_text(encoding="utf-8")
    )
    workloads.append(assignment)

    for deployment in workloads:
        for container in deployment["spec"]["template"]["spec"]["containers"]:
            assert container["readinessProbe"]["httpGet"]["path"] == "/health"
            assert container["livenessProbe"]["httpGet"]["path"] == "/health"
            assert container["resources"]["requests"]
            assert container["resources"]["limits"]
            assert any(
                source.get("configMapRef", {}).get("name") == "aceest-config"
                for source in container.get("envFrom", [])
            )
