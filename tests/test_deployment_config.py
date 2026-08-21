from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_production_compose_does_not_mount_docker_socket() -> None:
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text()

    assert "/var/run/docker.sock" not in compose
    assert "--providers.docker=true" not in compose
    assert "--providers.file.filename=/etc/traefik/dynamic.yml" in compose
    assert 'user: "65532:65532"' in compose
    assert '"80:8080"' in compose
    assert '"443:8443"' in compose


def test_traefik_template_keeps_share_links_public_and_uploads_protected() -> None:
    template = (PROJECT_ROOT / "deploy" / "traefik" / "dynamic.yml.template").read_text()

    assert 'Path(`/`) || PathPrefix(`/api/uploads`) || PathPrefix(`/admin`)' in template
    assert "usersFile: /run/secrets/upload-users.htpasswd" in template
    assert "media-uploader-public:" in template
    assert "strip-authorization" in template
    assert 'Authorization: ""' in template
    assert "Host(`@@UPLOAD_HOST@@`)" in template
    assert 'url: "http://web:5000"' in template


def test_traefik_renderer_substitutes_only_validated_values(tmp_path: Path) -> None:
    output = tmp_path / "dynamic.yml"
    script = PROJECT_ROOT / "deploy" / "traefik" / "render-dynamic-config.sh"

    subprocess.run(
        [str(script), "files.example.com", "7340032", str(output)],
        check=True,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )

    rendered = output.read_text()
    assert "@@UPLOAD_HOST@@" not in rendered
    assert "@@MAX_REQUEST_SIZE@@" not in rendered
    assert "Host(`files.example.com`)" in rendered
    assert "maxRequestBodyBytes: 7340032" in rendered


def test_traefik_renderer_rejects_host_injection(tmp_path: Path) -> None:
    output = tmp_path / "dynamic.yml"
    script = PROJECT_ROOT / "deploy" / "traefik" / "render-dynamic-config.sh"

    result = subprocess.run(
        [str(script), "bad.example.com`) || PathPrefix(`/", "7340032", str(output)],
        check=False,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert not output.exists()
