from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import aiohttp
import asyncio
import json
import tiktoken
from .base_model import (
    BaseAIModel,
    ModelStatus,
    ModelError,
    ResponseGenerationError,
    ModelInitializationError
)


class OpenAIError(ModelError):
    """Error raised for OpenAI API specific issues"""
    pass


class TokenLimitError(ModelError):
    """Error raised when token limit is exceeded"""
    pass


class GPTModel(BaseAIModel):
    """
    GPT model implementation using OpenAI's API.
    Handles API communication, token management, and rate limiting.
    """

    # OpenAI API endpoints
    API_URL = "https://api.openai.com/v1"
    MODELS_ENDPOINT = f"{API_URL}/models"
    COMPLETIONS_ENDPOINT = f"{API_URL}/chat/completions"

    # Token limits for different models
    TOKEN_LIMITS = {
        "gpt-3.5-turbo": 4096,
        "gpt-4": 8192,
        "gpt-4-32k": 32768
    }

    def __init__(self, model_config: Dict[str, Any]):
        """
        Initialize GPT model with configuration.

        Args:
            model_config: Model configuration
                Required keys:
                - api_key (str): OpenAI API key
                - model_name (str): GPT model name
                - temperature (float): Response randomness
                - max_tokens (int): Maximum tokens per response
                - request_timeout (int): API request timeout in seconds
                - rate_limit_rpm (int): Rate limit requests per minute
                - retry_config (Dict): Retry configuration
        """
        super().__init__(model_config)

        # API configuration
        self.api_key = model_config['api_key']
        self.model_name = model_config.get('model_name', 'gpt-3.5-turbo')
        self.temperature = model_config.get('temperature', 0.7)
        self.request_timeout = model_config.get('request_timeout', 30)

        # Rate limiting
        self.rate_limit_rpm = model_config.get('rate_limit_rpm', 60)
        self.request_timestamps: List[datetime] = []

        # Retry configuration
        self.retry_config = model_config.get('retry_config', {
            'max_retries': 3,
            'initial_delay': 1,
            'max_delay': 10,
            'exponential_base': 2
        })

        # HTTP session
        self._session: Optional[aiohttp.ClientSession] = None
        self._tokenizer: Optional[tiktoken.Encoding] = None

    async def initialize(self) -> None:
        """
        Initialize the GPT model.
        - Create HTTP session
        - Verify API key
        - Initialize tokenizer
        - Check model availability

        Raises:
            ModelInitializationError: If initialization fails
        """
        try:
            self.status = ModelStatus.INITIALIZING

            # Initialize HTTP session
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                timeout=aiohttp.ClientTimeout(total=self.request_timeout)
            )

            # Initialize tokenizer
            self._tokenizer = tiktoken.encoding_for_model(self.model_name)

            # Verify API key and model availability
            await self._verify_api_access()

            self.status = ModelStatus.READY
            self.logger.info(f"GPT Model {self.model_name} initialized successfully")

        except Exception as e:
            self.status = ModelStatus.ERROR
            raise ModelInitializationError(f"Failed to initialize GPT model: {str(e)}") from e

    async def cleanup(self) -> None:
        """
        Clean up model resources.
        - Close HTTP session
        - Clear caches
        - Reset rate limiting

        Raises:
            ModelCleanupError: If cleanup fails
        """
        try:
            self.status = ModelStatus.CLEANING

            if self._session:
                await self._session.close()
                self._session = None

            self._tokenizer = None
            self.request_timestamps.clear()
            self._response_cache.clear()

            self.logger.info(f"GPT Model {self.model_name} cleaned up successfully")

        except Exception as e:
            raise ModelCleanupError(f"Failed to clean up GPT model: {str(e)}") from e

    async def _generate_response_internal(self, prompt: str, context: Dict[str, Any]) -> str:
        """
        Generate response using OpenAI's API.

        Args:
            prompt: Input prompt
            context: Conversation context

        Returns:
            str: Generated response

        Raises:
            ResponseGenerationError: If generation fails
            TokenLimitError: If token limit is exceeded
            OpenAIError: If API request fails
        """
        try:
            # Check rate limit
            await self._check_rate_limit()

            # Prepare messages
            messages = self._prepare_messages(prompt, context)

            # Check token limit
            token_count = self._count_tokens(messages)
            max_tokens = self.TOKEN_LIMITS.get(self.model_name, 4096)
            if token_count > max_tokens:
                raise TokenLimitError(f"Token count {token_count} exceeds limit {max_tokens}")

            # Make API request with retry logic
            response = await self._make_api_request_with_retry(messages)

            # Update rate limiting
            self.request_timestamps.append(datetime.now())

            # Update token usage metrics
            self.performance_metrics['token_usage'] += token_count

            return response['choices'][0]['message']['content']

        except Exception as e:
            self.error_count += 1
            raise ResponseGenerationError(f"Failed to generate response: {str(e)}") from e

    async def _verify_api_access(self) -> None:
        """
        Verify API key and model availability.

        Raises:
            ModelInitializationError: If verification fails
        """
        if not self._session:
            raise ModelInitializationError("HTTP session not initialized")

        try:
            async with self._session.get(self.MODELS_ENDPOINT) as response:
                if response.status != 200:
                    raise ModelInitializationError(
                        f"API key verification failed with status {response.status}"
                    )
                models = await response.json()
                if not any(model['id'] == self.model_name for model in models['data']):
                    raise ModelInitializationError(f"Model {self.model_name} not available")

        except aiohttp.ClientError as e:
            raise ModelInitializationError(f"API access verification failed: {str(e)}")

    def _prepare_messages(self, prompt: str, context: Dict[str, Any]) -> List[Dict[str, str]]:
        """
        Prepare messages for API request.

        Args:
            prompt: User prompt
            context: Conversation context

        Returns:
            List[Dict[str, str]]: Formatted messages
        """
        messages = [
            {"role": "system", "content": context['system_prompt']},
            *[
                {"role": msg['role'], "content": msg['content']}
                for msg in context['conversation_history'][-5:]  # Last 5 messages
            ],
            {"role": "user", "content": prompt}
        ]
        return messages

    def _count_tokens(self, messages: List[Dict[str, str]]) -> int:
        """
        Count tokens in messages.

        Args:
            messages: List of messages

        Returns:
            int: Token count
        """
        if not self._tokenizer:
            raise ModelError("Tokenizer not initialized")

        text = json.dumps(messages)
        return len(self._tokenizer.encode(text))

    async def _check_rate_limit(self) -> None:
        """
        Check and enforce rate limiting.

        Raises:
            ResponseGenerationError: If rate limit is exceeded
        """
        now = datetime.now()
        # Remove timestamps older than 1 minute
        self.request_timestamps = [
            ts for ts in self.request_timestamps
            if (now - ts).total_seconds() < 60
        ]

        if len(self.request_timestamps) >= self.rate_limit_rpm:
            raise ResponseGenerationError("Rate limit exceeded")

    async def _make_api_request_with_retry(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        if not self._session:
            raise ModelError("HTTP session not initialized")

        last_error = None
        for attempt in range(self.retry_config['max_retries'] + 1):
            try:
                async with self._session.post(
                        self.COMPLETIONS_ENDPOINT,
                        json={
                            "model": self.model_name,
                            "messages": messages,
                            "temperature": self.temperature,
                            "max_tokens": self.config['max_tokens']
                        },
                        timeout=aiohttp.ClientTimeout(total=self.request_timeout)
                ) as response:
                    if response.status == 200:
                        return await response.json()

                    error_data = await response.json()
                    raise OpenAIError(f"API request failed: {response.status} - {error_data}")

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = e
                if attempt < self.retry_config['max_retries']:
                    delay = min(
                        self.retry_config['initial_delay'] *
                        (self.retry_config['exponential_base'] ** attempt),
                        self.retry_config['max_delay']
                    )
                    await asyncio.sleep(delay)
                    continue

        raise OpenAIError(f"Max retries exceeded: {last_error}")