import ast
from pathlib import Path

from apps.groups.evolution_client import EvolutionReadOnlyClient

FORBIDDEN_ENDPOINTS = {
    "sendtext",
    "sendmedia",
    "sendaudio",
    "sendvoice",
    "sendlocation",
    "sendcontact",
    "sendpoll",
    "sendbutton",
    "sendlist",
}


def test_ast_scan_no_message_sending_endpoints_in_codebase():
    """
    Varredura estática de AST em todo o código-fonte da aplicação para garantir
    que NENHUM endpoint de envio de mensagem da Evolution API exista ou seja chamado.
    """
    root_dir = Path(__file__).resolve().parent.parent / "apps"
    violations = []

    for py_file in root_dir.rglob("*.py"):
        if "test" in py_file.name:
            continue

        with open(py_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Checagem textual rápida
        lower_content = content.lower()
        for forbidden in FORBIDDEN_ENDPOINTS:
            if forbidden in lower_content:
                violations.append(f"{py_file.name}: Encontrada referência proibida a '{forbidden}'")

        # Checagem de AST para chamadas de método
        try:
            tree = ast.parse(content, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func_name = ""
                    if isinstance(node.func, ast.Name):
                        func_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        func_name = node.func.attr
                    if func_name.lower() in FORBIDDEN_ENDPOINTS:
                        violations.append(
                            f"{py_file.name}: Chamada proibida a função/método '{func_name}'"
                        )
        except SyntaxError:
            pass

    assert not violations, (
        "Violação da política de sistema estritamente somente-leitura:\n" + "\n".join(violations)
    )


def test_evolution_client_has_only_readonly_methods():
    """Garante que o EvolutionReadOnlyClient contém apenas métodos de consulta."""
    client = EvolutionReadOnlyClient()
    method_names = [m for m in dir(client) if not m.startswith("_")]

    assert "get_connection_state" in method_names
    assert "fetch_all_groups" in method_names
    assert "get_group_participants" in method_names

    for m in method_names:
        assert "send" not in m.lower(), f"Método de envio proibido detectado no client: {m}"
