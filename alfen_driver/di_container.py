"""Dependency Injection container for the Alfen EV Charger Driver.

This module implements a lightweight dependency injection framework that improves
testability, maintainability, and modularity of the driver. It provides both
manual dependency injection and automatic dependency resolution based on type hints.

Key Features:
    - Interface-based dependency injection
    - Automatic lifetime management (singleton, transient, scoped)
    - Constructor injection with type hint resolution
    - Mock-friendly design for testing
    - Configuration-based service registration
    - Circular dependency detection

Example:
    ```python
    from alfen_driver.di_container import DIContainer, ServiceLifetime

    # Create and configure container
    container = DIContainer()
    container.register(IModbusClient, ModbusTcpClientWrapper, ServiceLifetime.SINGLETON)
    container.register(ILogger, StructuredLogger, ServiceLifetime.SINGLETON)

    # Register instance
    container.register_instance(Config, load_config())

    # Resolve dependencies
    driver = container.resolve(AlfenDriver)
    ```
"""

import inspect
import threading
from collections import defaultdict
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Type,
    TypeVar,
    get_type_hints,
)


class ServiceLifetime(Enum):
    """Defines the lifetime management strategy for injected services.

    Attributes:
        TRANSIENT: Create a new instance every time the service is requested.
        SINGLETON: Create one instance and reuse it for all subsequent requests.
        SCOPED: Create one instance per scope (useful for request-scoped services).
    """

    TRANSIENT = "transient"
    SINGLETON = "singleton"
    SCOPED = "scoped"


class DIError(Exception):
    """Base exception for dependency injection errors."""

    pass


class CircularDependencyError(DIError):
    """Raised when circular dependencies are detected during resolution."""

    def __init__(self, dependency_chain: List[str]) -> None:
        self.dependency_chain = dependency_chain
        chain_str = " -> ".join(dependency_chain)
        super().__init__(f"Circular dependency detected: {chain_str}")


class ServiceRegistration:
    """Represents a service registration in the DI container.

    Attributes:
        interface: The interface or abstract type being registered.
        implementation: The concrete implementation type.
        lifetime: The lifetime management strategy.
        factory: Optional factory function for custom instantiation.
        instance: Optional pre-created instance (for singleton pattern).
    """

    def __init__(
        self,
        interface: Type,
        implementation: Optional[Type] = None,
        lifetime: ServiceLifetime = ServiceLifetime.TRANSIENT,
        factory: Optional[Callable[[], Any]] = None,
        instance: Optional[Any] = None,
    ) -> None:
        self.interface = interface
        self.implementation = implementation or interface
        self.lifetime = lifetime
        self.factory = factory
        self.instance = instance
        self._singleton_instance: Optional[Any] = None
        self._lock = threading.Lock()


T = TypeVar("T")


class DIContainer:
    """Lightweight dependency injection container.

    This container provides dependency injection capabilities with support for
    different service lifetimes, automatic constructor injection based on type
    hints, and comprehensive error handling for circular dependencies.

    The container supports both interface-based and concrete type registration,
    making it suitable for both production code and testing scenarios.

    Example:
        ```python
        # Create container
        container = DIContainer()

        # Register services
        container.register(IModbusClient, RealModbusClient, ServiceLifetime.SINGLETON)
        container.register(ILogger, StructuredLogger, ServiceLifetime.SINGLETON)

        # Register configuration instance
        config = load_config()
        container.register_instance(Config, config)

        # Resolve with automatic dependency injection
        driver = container.resolve(AlfenDriver)
        ```
    """

    def __init__(self) -> None:
        self._registrations: Dict[Type, ServiceRegistration] = {}
        self._scoped_instances: Dict[str, Dict[Type, Any]] = defaultdict(dict)
        self._resolution_stack: List[str] = []
        self._lock = threading.Lock()

    def register(
        self,
        interface: Type[Any],
        implementation: Optional[Type[Any]] = None,
        lifetime: ServiceLifetime = ServiceLifetime.TRANSIENT,
    ) -> "DIContainer":
        """Register a service implementation for an interface.

        Args:
            interface: The interface or type to register.
            implementation: The concrete implementation. If None, uses interface.
            lifetime: The lifetime management strategy.

        Returns:
            Self for method chaining.

        Example:
            ```python
            container.register(
                IModbusClient, ModbusTcpClientWrapper, ServiceLifetime.SINGLETON
            )
            ```
        """
        with self._lock:
            registration = ServiceRegistration(interface, implementation, lifetime)
            self._registrations[interface] = registration
        return self

    def register_factory(
        self,
        interface: Type[Any],
        factory: Callable[[], Any],
        lifetime: ServiceLifetime = ServiceLifetime.TRANSIENT,
    ) -> "DIContainer":
        """Register a factory function for creating service instances.

        Args:
            interface: The interface or type to register.
            factory: Factory function that creates instances.
            lifetime: The lifetime management strategy.

        Returns:
            Self for method chaining.

        Example:
            ```python
            def create_modbus_client():
                return ModbusTcpClient(host="192.168.1.100", port=502)

            container.register_factory(
                IModbusClient, create_modbus_client, ServiceLifetime.SINGLETON
            )
            ```
        """
        with self._lock:
            registration = ServiceRegistration(interface, None, lifetime, factory)
            self._registrations[interface] = registration
        return self

    def register_instance(self, interface: Type[Any], instance: Any) -> "DIContainer":
        """Register a pre-created instance.

        Args:
            interface: The interface or type to register.
            instance: The pre-created instance to use.

        Returns:
            Self for method chaining.

        Example:
            ```python
            config = load_config()
            container.register_instance(Config, config)
            ```
        """
        with self._lock:
            registration = ServiceRegistration(
                interface, type(instance), ServiceLifetime.SINGLETON, instance=instance
            )
            registration._singleton_instance = instance
            self._registrations[interface] = registration
        return self

    def resolve(self, service_type: Type[Any], scope_id: Optional[str] = None) -> Any:
        """Resolve a service instance with automatic dependency injection.

        Args:
            service_type: The type of service to resolve.
            scope_id: Optional scope identifier for scoped services.

        Returns:
            An instance of the requested service with all dependencies injected.

        Raises:
            DIError: If the service is not registered or cannot be resolved.
            CircularDependencyError: If circular dependencies are detected.

        Example:
            ```python
            driver = container.resolve(AlfenDriver)
            ```
        """
        return self._resolve_internal(service_type, scope_id)

    def _resolve_internal(
        self, service_type: Type[Any], scope_id: Optional[str] = None
    ) -> Any:
        """Internal resolution method with circular dependency detection."""
        type_name = service_type.__name__

        # Check for circular dependencies
        if type_name in self._resolution_stack:
            cycle_start = self._resolution_stack.index(type_name)
            cycle = self._resolution_stack[cycle_start:] + [type_name]
            raise CircularDependencyError(cycle)

        self._resolution_stack.append(type_name)

        try:
            return self._create_instance(service_type, scope_id)
        finally:
            self._resolution_stack.pop()

    def _create_instance(
        self, service_type: Type[Any], scope_id: Optional[str] = None
    ) -> Any:
        """Create an instance based on registration and lifetime management."""
        registration = self._registrations.get(service_type)

        if not registration:
            # Try to create instance without registration (for concrete classes)
            return self._create_unregistered_instance(service_type, scope_id)

        # Handle different lifetime strategies
        if registration.lifetime == ServiceLifetime.SINGLETON:
            return self._get_singleton_instance(registration, scope_id)
        elif registration.lifetime == ServiceLifetime.SCOPED and scope_id:
            return self._get_scoped_instance(registration, scope_id)
        else:
            return self._create_new_instance(registration, scope_id)

    def _get_singleton_instance(
        self, registration: ServiceRegistration, scope_id: Optional[str] = None
    ) -> Any:
        """Get or create singleton instance with thread safety."""
        instance = registration._singleton_instance
        if instance is not None:
            return instance

        with registration._lock:
            instance = registration._singleton_instance
            if instance is None:
                instance = self._create_new_instance(registration, scope_id)
                registration._singleton_instance = instance
            return instance

    def _get_scoped_instance(
        self, registration: ServiceRegistration, scope_id: str
    ) -> Any:
        """Get or create scoped instance."""
        scoped_instances = self._scoped_instances[scope_id]

        if registration.interface in scoped_instances:
            return scoped_instances[registration.interface]

        instance = self._create_new_instance(registration, scope_id)
        scoped_instances[registration.interface] = instance
        return instance

    def _create_new_instance(
        self, registration: ServiceRegistration, scope_id: Optional[str] = None
    ) -> Any:
        """Create a new instance using factory or constructor injection."""
        if registration.instance is not None:
            return registration.instance

        if registration.factory:
            return registration.factory()

        return self._create_with_constructor_injection(
            registration.implementation, scope_id
        )

    def _create_unregistered_instance(
        self, service_type: Type[Any], scope_id: Optional[str] = None
    ) -> Any:
        """Create instance for unregistered concrete class."""
        if inspect.isabstract(service_type):
            raise DIError(
                f"Cannot instantiate abstract class {service_type.__name__} "
                f"without registration"
            )

        return self._create_with_constructor_injection(service_type, scope_id)

    def _create_with_constructor_injection(
        self, implementation_type: Type[Any], scope_id: Optional[str] = None
    ) -> Any:
        """Create instance with automatic constructor dependency injection."""
        constructor = implementation_type.__init__
        signature = inspect.signature(constructor)

        # Get type hints for constructor parameters
        type_hints = get_type_hints(constructor)

        # Resolve constructor parameters
        kwargs = {}
        for param_name, param in signature.parameters.items():
            if param_name == "self":
                continue

            param_type = type_hints.get(param_name)
            if param_type:
                # Resolve dependency
                kwargs[param_name] = self._resolve_internal(param_type, scope_id)
            elif param.default != inspect.Parameter.empty:
                # Use default value if available
                kwargs[param_name] = param.default
            else:
                raise DIError(
                    f"Cannot resolve parameter '{param_name}' for "
                    f"{implementation_type.__name__}. "
                    f"Missing type hint or default value."
                )

        return implementation_type(**kwargs)

    def is_registered(self, service_type: Type) -> bool:
        """Check if a service type is registered in the container."""
        return service_type in self._registrations

    def clear_scope(self, scope_id: str) -> None:
        """Clear all scoped instances for the given scope ID."""
        if scope_id in self._scoped_instances:
            del self._scoped_instances[scope_id]

    def clear(self) -> None:
        """Clear all registrations and cached instances."""
        with self._lock:
            self._registrations.clear()
            self._scoped_instances.clear()

    def get_registered_services(self) -> Dict[Type, ServiceRegistration]:
        """Get a copy of all registered services for inspection."""
        with self._lock:
            return self._registrations.copy()


# Global container instance for application-wide dependency injection
_global_container: Optional[DIContainer] = None
_container_lock = threading.Lock()


def get_container() -> DIContainer:
    """Get the global DI container instance.

    Returns:
        The global DIContainer instance, creating it if necessary.

    Example:
        ```python
        container = get_container()
        container.register(IModbusClient, ModbusTcpClientWrapper)
        ```
    """
    global _global_container

    if _global_container is None:
        with _container_lock:
            if _global_container is None:
                _global_container = DIContainer()

    return _global_container


def reset_container() -> None:
    """Reset the global container (useful for testing).

    Example:
        ```python
        # In test setup
        reset_container()
        container = get_container()
        container.register(IModbusClient, MockModbusClient)
        ```
    """
    global _global_container

    with _container_lock:
        _global_container = None


def injectable(cls: Type[T]) -> Type[T]:
    """Class decorator to mark classes as injectable.

    This decorator can be used to explicitly mark classes that should
    support dependency injection, improving code clarity.

    Args:
        cls: The class to mark as injectable.

    Returns:
        The same class, unchanged.

    Example:
        ```python
        @injectable
        class AlfenDriver:
            def __init__(self, modbus_client: IModbusClient, logger: ILogger):
                self.modbus_client = modbus_client
                self.logger = logger
        ```
    """
    # Add metadata to mark class as injectable without upsetting type checkers
    setattr(cls, "_is_injectable", True)
    return cls
