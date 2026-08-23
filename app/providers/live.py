import os
from .base import Provider
class UnavailableProvider(Provider):
    def __init__(self,name,required): self.name=name; self.required=required
    def fetch(self,cursor=None):
        missing=[k for k in self.required if not os.getenv(k)]
        if missing: raise ProviderUnavailable(f"{self.name}: 未設定: {', '.join(missing)}")
        raise ProviderUnavailable(f"{self.name}: Live adapter は契約仕様に合わせて実装してください")
class ProviderUnavailable(RuntimeError): pass
