import pytest
from src.ai.models.base_model import BaseAIModel
from src.ai.models.gpt_model import GPTModel


@pytest.fixture
def model_config():
    return {
        "model_name": "gpt-3.5-turbo",
        "temperature": 0.7,
        "max_tokens": 150
    }


@pytest.mark.asyncio
async def test_gpt_model(model_config):
    model = GPTModel(model_config)

    # Test initialization
    assert model.model_name == model_config["model_name"]
    assert model.temperature == model_config["temperature"]

    # Test response generation
    test_prompt = "Hello, how are you?"
    context = {"conversation_history": []}
    response = await model.generate_response(test_prompt, context)
    assert isinstance(response, str)
    assert len(response) > 0


@pytest.mark.asyncio
async def test_model_lifecycle(model_config):
    model = GPTModel(model_config)

    # Test initialization
    await model.initialize()

    # Test cleanup
    await model.cleanup()