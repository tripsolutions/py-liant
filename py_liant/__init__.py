from .json_object import JsonObject
from .json_decoder import JSONDecoder
from .json_encoder import JSONEncoder
from .monkeypatch import patch_sqlalchemy_base_class
from .pyramid import (
    CRUDView, pyramid_json_renderer_factory, pyramid_json_decoder)

__all__ = ["JsonObject", "JSONDecoder", "JSONEncoder",
           "patch_sqlalchemy_base_class", "CRUDView",
           "pyramid_json_renderer_factory", "pyramid_json_decoder"]


# example usage as pyramid configuration function
# def includeme(config):
#     config.add_renderer('json', pyramid_json_renderer_factory(Base))
#     config.add_request_method(pyramid_json_decoder, 'json', reify=True)
#     patch_sqlalchemy_base_class(Base)
