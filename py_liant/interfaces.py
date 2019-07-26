from abc import ABC, abstractmethod


class ChangeGuardProvider(ABC):
    @abstractmethod
    def guard(self, obj, data, for_update=True):
        pass
