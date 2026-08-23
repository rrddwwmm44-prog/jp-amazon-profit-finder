from abc import ABC, abstractmethod
from app.domain import ProductSignal
class Provider(ABC):
    name="base"
    @abstractmethod
    def fetch(self, cursor: str|None=None) -> tuple[list[ProductSignal],str|None]: ...
