from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import logging
import json
from enum import Enum


class ModelStatus(Enum):
    """
    Enum representing the possible states of the AI model.
    """
    UNINITIALIZED = "uninitialized"
    INITIALIZING = "initializing"
    READY = "ready"
    PROCESSING = "processing"
    ERROR = "error"
    CLEANING = "cleaning"


class BaseAIModel(ABC):
    """
    Abstract base class for AI model implementations.
    All AI model implementations must inherit from this class.

    Attributes:
        config (Dict[str, Any]): Model configuration
        model_name (str): Name of the model
        status (ModelStatus): Current status of the model
        last_response_time (Optional[datetime]): Timestamp of the last response
        response_history (List[Dict[str, Any]]): History of responses
        error_count (int): Count of errors encountered
        performance_metrics (Dict[str, float]): Model performance metrics
    """

    def __init__(self, model_config: Dict[str, Any]):
        """
        Initialize the base AI model with configuration.

        Args:
            model_config: Model configuration dictionary
                Required keys:
                - model_name (str): Name of the model
                - temperature (float): Response randomness (0.0 to 1.0)
                - max_tokens (int): Maximum token limit
                - context_window (int): Context window size
                - response_timeout (int): Maximum response time in seconds
                - retry_attempts (int): Number of retry attempts
                - batch_size (int): Batch processing size
                - use_cache (bool): Enable response caching
                - performance_monitoring (bool): Enable performance monitoring
        """
        self._validate_config(model_config)
        self.config = model_config
        self.model_name = model_config['model_name']
        self.status = ModelStatus.UNINITIALIZED
        self.last_response_time = None
        self.response_history: List[Dict[str, Any]] = []
        self.error_count = 0
        self.performance_metrics = {
            'average_response_time': 0.0,
            'success_rate': 100.0,
            'token_usage': 0,
            'cache_hit_rate': 0.0
        }
        self.logger = logging.getLogger(f"{self.__class__.__name__}")
        self._response_cache: Dict[str, str] = {}

    @staticmethod
    def _validate_config(config: Dict[str, Any]) -> None:
        """
        Validate the model configuration.

        Args:
            config: Configuration to validate

        Raises:
            ValueError: If required settings are missing or invalid
            TypeError: If settings have incorrect types
        """
        required_keys = {
            'model_name': str,
            'temperature': float,
            'max_tokens': int,
            'context_window': int,
            'response_timeout': int,
            'retry_attempts': int,
            'batch_size': int,
            'use_cache': bool,
            'performance_monitoring': bool
        }

        for key, expected_type in required_keys.items():
            if key not in config:
                raise ValueError(f"Missing required config key: {key}")
            if not isinstance(config[key], expected_type):
                raise TypeError(f"Config key {key} must be of type {expected_type}")

        if not (0.0 <= config['temperature'] <= 1.0):
            raise ValueError("Temperature must be between 0.0 and 1.0")

        if any(config[key] <= 0 for key in
               ['max_tokens', 'context_window', 'response_timeout', 'retry_attempts', 'batch_size']):
            raise ValueError("Numeric config values must be positive")

    @abstractmethod
    async def generate_response(self, prompt: str, context: Dict[str, Any]) -> str:
        """
        Generate a response based on the prompt and context.

        Args:
            prompt: Input prompt for response generation
            context: Conversation context information
                Required keys:
                - conversation_history (List[Dict]): Previous conversation records
                - system_prompt (str): System-level prompt
                - user_preferences (Dict): User-specific settings
                - metadata (Dict): Additional context metadata
                - constraints (Dict): Response generation constraints

        Returns:
            str: Generated response text

        Raises:
            ModelNotInitializedError: If model is not initialized
            ResponseGenerationError: If response generation fails
            ResponseTimeoutError: If response generation times out
            InvalidInputError: If prompt or context is invalid
        """
        self._check_model_status()
        self._validate_input(prompt, context)

        cache_key = self._generate_cache_key(prompt, context)
        if self.config['use_cache'] and cache_key in self._response_cache:
            return self._response_cache[cache_key]

        start_time = datetime.now()
        try:
            self.status = ModelStatus.PROCESSING
            response = await self._generate_response_internal(prompt, context)
            self._update_metrics(start_time)

            if self.config['use_cache']:
                self._response_cache[cache_key] = response

            return response
        except Exception as e:
            self.error_count += 1
            self.status = ModelStatus.ERROR
            self._update_error_metrics(e)
            raise
        finally:
            self.status = ModelStatus.READY

    @abstractmethod
    async def initialize(self) -> None:
        """
        Initialize the model resources and settings.

        Performs:
        - Model weights loading
        - Resource allocation
        - Cache initialization
        - Performance monitoring setup
        - Connection establishment

        Raises:
            ModelInitializationError: If initialization fails
            ResourceAllocationError: If resource allocation fails
            ConnectionError: If required connections fail
        """
        self.status = ModelStatus.INITIALIZING
        self.logger.info(f"Initializing {self.model_name}")

    @abstractmethod
    async def cleanup(self) -> None:
        """
        Clean up model resources.

        Performs:
        - Resource deallocation
        - Cache clearing
        - Connection closing
        - Temporary file removal
        - Performance metric logging

        Raises:
            ModelCleanupError: If cleanup fails
            ResourceReleaseError: If resource release fails
            ConnectionCleanupError: If connection cleanup fails
        """
        self.status = ModelStatus.CLEANING
        self.logger.info(f"Cleaning up {self.model_name}")

    async def _generate_response_internal(self, prompt: str, context: Dict[str, Any]) -> str:
        """
        Internal method for response generation.
        Must be implemented by subclasses.

        Args:
            prompt: Input prompt
            context: Context information

        Returns:
            str: Generated response

        Raises:
            NotImplementedError: If not implemented by subclass
        """
        raise NotImplementedError("_generate_response_internal must be implemented by subclass")

    def _check_model_status(self) -> None:
        """
        Check if the model is in a valid state for response generation.

        Raises:
            ModelNotInitializedError: If model is not initialized
            ModelBusyError: If model is currently processing
            ModelErrorState: If model is in error state
        """
        if self.status != ModelStatus.READY:
            raise ModelNotInitializedError(f"Model is in {self.status.value} state")

    def _validate_input(self, prompt: str, context: Dict[str, Any]) -> None:
        """
        Validate input parameters.

        Args:
            prompt: Input prompt to validate
            context: Context to validate

        Raises:
            ValueError: If input is invalid
            TypeError: If input has incorrect type
        """
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt must be a non-empty string")

        required_context = {
            'conversation_history': list,
            'system_prompt': str,
            'user_preferences': dict,
            'metadata': dict,
            'constraints': dict
        }

        for key, expected_type in required_context.items():
            if key not in context:
                raise ValueError(f"Missing required context key: {key}")
            if not isinstance(context[key], expected_type):
                raise TypeError(f"Context key {key} must be of type {expected_type}")

    def _generate_cache_key(self, prompt: str, context: Dict[str, Any]) -> str:
        """
        Generate a cache key for the prompt and context.

        Args:
            prompt: Input prompt
            context: Context information

        Returns:
            str: Cache key
        """
        cache_data = {
            'prompt': prompt,
            'context': {
                'system_prompt': context['system_prompt'],
                'constraints': context['constraints']
            }
        }
        return json.dumps(cache_data, sort_keys=True)

    def _update_metrics(self, start_time: datetime) -> None:
        """
        Update performance metrics after response generation.

        Args:
            start_time: Start time of response generation
        """
        response_time = (datetime.now() - start_time).total_seconds()
        self.performance_metrics['average_response_time'] = (
                                                                    self.performance_metrics[
                                                                        'average_response_time'] * len(
                                                                self.response_history) + response_time
                                                            ) / (len(self.response_history) + 1)

        if self._response_cache:
            cache_hits = sum(1 for r in self.response_history if r.get('cached', False))
            self.performance_metrics['cache_hit_rate'] = cache_hits / len(self.response_history) * 100

    def _update_error_metrics(self, error: Exception) -> None:
        """
        Update error-related metrics.

        Args:
            error: Exception that occurred
        """
        self.performance_metrics['success_rate'] = (
            (len(self.response_history) - self.error_count) / len(self.response_history) * 100
            if self.response_history else 0.0
        )
        self.logger.error(f"Error in {self.model_name}: {str(error)}")


class ModelError(Exception):
    """Base class for model-related errors"""
    pass


class ModelNotInitializedError(ModelError):
    """Error raised when model is not initialized"""
    pass


class ModelInitializationError(ModelError):
    """Error raised during model initialization"""
    pass


class ModelCleanupError(ModelError):
    """Error raised during model cleanup"""
    pass


class ResponseGenerationError(ModelError):
    """Error raised during response generation"""
    pass


class ResponseTimeoutError(ModelError):
    """Error raised when response generation times out"""
    pass


class ResourceAllocationError(ModelError):
    """Error raised during resource allocation"""
    pass


class ResourceReleaseError(ModelError):
    """Error raised during resource release"""
    pass


class InvalidInputError(ModelError):
    """Error raised for invalid input"""
    pass