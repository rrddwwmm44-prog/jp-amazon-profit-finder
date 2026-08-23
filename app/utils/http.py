from __future__ import annotations
import json, random, time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

class HttpError(RuntimeError):
    def __init__(self, status: int|None, message: str): self.status=status; super().__init__(message)

class JsonHttpClient:
    def __init__(self, timeout=15.0, retries=3, min_interval=1.05):
        self.timeout=timeout; self.retries=retries; self.min_interval=min_interval; self._last=0.0
    def get(self, url: str, params: dict, headers: dict|None=None) -> dict:
        target=f"{url}?{urlencode({k:v for k,v in params.items() if v is not None})}"
        for attempt in range(self.retries+1):
            delay=self.min_interval-(time.monotonic()-self._last)
            if delay>0: time.sleep(delay)
            try:
                self._last=time.monotonic()
                req=Request(target,headers={"Accept":"application/json","User-Agent":"jp-amazon-profit-finder/0.1",**(headers or {})})
                with urlopen(req,timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as e:
                body=e.read().decode("utf-8",errors="replace")[:500]
                if e.code not in (429,500,502,503,504) or attempt==self.retries: raise HttpError(e.code,body) from e
            except (URLError,TimeoutError) as e:
                if attempt==self.retries: raise HttpError(None,str(e)) from e
            time.sleep(min(8.0,2**attempt+random.random()))
        raise AssertionError("unreachable")
