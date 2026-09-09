"""Artifact identity is shared by search, graph and Task views; a binding owns only a connection."""
import asyncio
import pytest
from uuid import UUID
from ax_workspace.platform.persistence import make_session_factory
from ax_workspace.platform.work_tasks import SqlAlchemyAttachmentRepository
from test_material_search import _stack, _upload
from test_material_evidence_owners import _turn
from ax_workspace.entrypoints.mcp import McpReportsFacade

MINA = {"X-Demo-Persona": "mina"}


@pytest.mark.parametrize("observe_second", [False, True])
def test_one_artifact_has_one_graph_node_and_independent_task_bindings(tmp_path, monkeypatch, observe_second):
    client, app, worker, settings = _stack(tmp_path)
    first = client.post('/api/tasks', headers=MINA, json={'title': '첫 연결'}).json()['task_id']
    second = client.post('/api/tasks', headers=MINA, json={'title': '두 번째 연결'}).json()['task_id']
    uploaded = _upload(client, first, 'shared.txt', b'canonicalidentitytoken', 'text/plain').json()
    artifact, binding = uploaded['material_id'], uploaded['binding_id']
    assert artifact != binding
    with make_session_factory(settings.database_url)() as session:
        other = SqlAlchemyAttachmentRepository(session).bind(attachment_id=UUID(artifact), context_type='task', context_id=second, role='input', bound_by='mina')
        session.commit()
        other_id = str(other.id)
    assert asyncio.run(worker.run_once())
    assert client.get(f'/api/tasks/{second}/materials', headers=MINA).json()[0]['material_id'] == artifact
    found = client.get('/api/materials/search', headers=MINA, params={'q': 'canonicalidentitytoken'}).json()
    assert len(found['results']) == 1 and found['results'][0]['material_id'] == artifact
    assert {r['binding_id'] for r in found['results'][0]['source_contexts']} == {binding, other_id}
    assert client.get(f'/api/tasks/{first}/materials/{artifact}/content', headers=MINA).content == b'canonicalidentitytoken'
    assert client.get(f'/api/tasks/{first}/materials/{binding}/content', headers=MINA).status_code == 404
    cid, _, eid = _turn(client, settings)
    monkeypatch.setenv('AX_MCP_CAUSATION_ID', str(eid))
    facade = McpReportsFacade(settings, 'mina')
    graph = facade.graph_neighbors('material:' + artifact)
    assert {r['from'] for r in graph['edges']} == {'task:' + first, 'task:' + second}
    assert all(r['to'] == 'material:' + artifact for r in graph['edges'])
    facade.list_task_materials(first)
    if observe_second:
        facade.list_task_materials(second)
    before = client.get(f'/api/conversations/{cid}', headers=MINA).json()
    assert any(r['resource_id'] == artifact for r in before['answer_resources'])
    assert client.post(f'/api/tasks/{first}/material-bindings/{binding}/detach', headers=MINA).status_code == 200
    after = client.get(f'/api/conversations/{cid}', headers=MINA).json()
    if observe_second:
        assert len(after['answer_resources']) == 1
        assert {r['resource_id'] for r in after['answer_resources'][0]['source_contexts']} == {second}
    else:
        assert after['answer_resources'] == []  # another readable context cannot restore this observation
    assert {r['from_ref'] for r in after['graph_receipts'] if r['kind']=='edge'} == {'task:' + second}
    assert client.get(f'/api/tasks/{first}/materials/{artifact}/content', headers=MINA).status_code == 404
    assert client.get(f'/api/tasks/{second}/materials/{artifact}/content', headers=MINA).status_code == 200
    assert client.get('/api/task-materials/search', headers=MINA, params={'q':'canonicalidentitytoken'}).status_code == 404
    assert client.get(f'/api/tasks/{second}/materials/search', headers=MINA, params={'q':'canonicalidentitytoken'}).status_code == 404


def test_repeated_bindings_do_not_duplicate_graph_edges(tmp_path):
    client, app, _, settings = _stack(tmp_path)
    task = client.post('/api/tasks', headers=MINA, json={'title': '역할 두 개'}).json()['task_id']
    uploaded = _upload(client, task, 'shared.txt', b'shared', 'text/plain').json()
    with make_session_factory(settings.database_url)() as session:
        SqlAlchemyAttachmentRepository(session).bind(attachment_id=UUID(uploaded['material_id']), context_type='task', context_id=task, role='output', bound_by='mina')
        session.commit()
    graph = McpReportsFacade(settings, 'mina').graph_neighbors('material:' + uploaded['material_id'])
    assert len(graph['edges']) == 1
    assert graph['truncated'] is False
    task_graph = McpReportsFacade(settings, 'mina').graph_neighbors('task:' + task, limit=2)
    edges = [row for row in task_graph['edges'] if row['kind'] == 'has_material']
    assert len(edges) == 1 and len(edges[0]['source_contexts']) == 2
    assert task_graph['truncated'] is False
    overview = McpReportsFacade(settings, 'mina').graph_overview(limit=240)
    assert len([row for row in overview['edges'] if row['kind'] == 'has_material' and row['to'] == 'material:' + uploaded['material_id']]) == 1


@pytest.mark.parametrize('change', ['rebind', 'integrity'])
def test_graph_material_observation_cannot_be_restored_by_a_different_binding_or_hash(tmp_path, monkeypatch, change):
    from ax_workspace.platform.persistence import AttachmentRecord
    client, _, _, settings = _stack(tmp_path)
    task = client.post('/api/tasks', headers=MINA, json={'title': '관측한 연결'}).json()['task_id']
    uploaded = _upload(client, task, 'observed.txt', b'observed', 'text/plain').json()
    cid, _, eid = _turn(client, settings)
    monkeypatch.setenv('AX_MCP_CAUSATION_ID', str(eid))
    facade = McpReportsFacade(settings, 'mina')
    facade.graph_neighbors('material:' + uploaded['material_id'])
    def material_seeds():
        pack = facade._application.conversation_context_pack(facade.principal, UUID(cid), include_exchanges=False)
        return [row for row in pack['seeds'] if row['ref'] == 'material:' + uploaded['material_id']]
    assert len(material_seeds()) == 1
    def material_edges():
        return [r for r in client.get(f'/api/conversations/{cid}', headers=MINA).json()['graph_receipts'] if r['edge_kind'] == 'has_material']
    assert len(material_edges()) == 1
    if change == 'rebind':
        client.post(f"/api/tasks/{task}/material-bindings/{uploaded['binding_id']}/detach", headers=MINA)
        assert material_edges() == []
    with make_session_factory(settings.database_url)() as session:
        if change == 'rebind':
            binding = SqlAlchemyAttachmentRepository(session).bind(attachment_id=UUID(uploaded['material_id']), context_type='task', context_id=task, role='input', bound_by='mina')
            assert str(binding.id) != uploaded['binding_id']
        else:
            session.get(AttachmentRecord, UUID(uploaded['material_id'])).integrity_ref = 'sha256:changed'
        session.commit()
    assert material_edges() == []
    assert material_seeds() == []
    facade.graph_neighbors('task:' + task)
    assert len(material_edges()) == 1  # only the new observation is readable
    assert len(material_seeds()) == 1
