from abc import ABC, abstractmethod


class JsonGuardProvider(ABC):
    @abstractmethod
    def guardUpdate(self, obj, data, for_update=True):
        pass

    @abstractmethod
    def guardHints(self, cls, hints):
        pass

    @abstractmethod
    def guardSerialize(self, obj, value):
        pass

    @abstractmethod
    def guardDrilldown(self, prop):
        pass
