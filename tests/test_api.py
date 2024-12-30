def test_get_conversation():
    conversation_id = "test-conversation-1"
    response = client.get(f"/conversations/{conversation_id}")
    assert response.status_code == 200
    assert "conversation_id" in response.json()
    assert "messages" in response.json()

def test_create_conversation():
    terminal_ids = {
        "terminal1": "test-terminal-1",
        "terminal2": "test-terminal-2"
    }
    response = client.post("/conversations", json=terminal_ids)
    assert response.status_code == 200
    assert "conversation_id" in response.json()

@pytest.mark.asyncio
async def test_websocket_connection():
    async with client.websocket_connect("/ws/test-terminal-1") as websocket:
        data = {"type": "connection_test", "content": "Hello"}
        await websocket.send_json(data)
        response = await websocket.receive_json()
        assert response["type"] == "connection_test"
        assert "timestamp" in response