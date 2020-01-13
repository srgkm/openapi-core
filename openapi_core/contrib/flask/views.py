"""OpenAPI core contrib flask views module"""
from flask.globals import request
from flask.views import MethodView

from openapi_core.contrib.flask.requests import FlaskOpenAPIRequestFactory
from openapi_core.contrib.flask.responses import FlaskOpenAPIResponseFactory
from openapi_core.schema.specs import Spec
from openapi_core.validators import RequestValidator, ResponseValidator
from openapi_core.wrappers import FlaskOpenAPIRequest, FlaskOpenAPIResponse


class OpenAPIView(MethodView):
    """Brings OpenAPI specification validation and marshalling for requests."""

    def __init__(self, spec: Spec):
        self.spec = spec
        self.request_validator = RequestValidator(spec)
        self.response_validator = ResponseValidator(spec)

    def dispatch_request(self, *args, **kwargs):
        errors = []

        openapi_request = FlaskOpenAPIRequestFactory.create(request)
        request_result = self.request_validator.validate(openapi_request)
        try:
            request_result.raise_for_errors()
        # return instantly on server error
        except InvalidServer as exc:
            errors.append(exc)
            response = self.handle_openapi_errors(errors)
        except OpenAPIMappingError as exc:
            errors.extend(request_result.errors)
            response = self.handle_openapi_errors(errors)
        else:
            request.parameters = request_result.parameters
            request.body = request_result.body

            response = super(OpenAPIHTTPMethodView, self).dispatch_request(
                *args, **kwargs)

        openapi_response = FlaskOpenAPIResponseFactory.create(response)
        response_result = self.response_validator.validate(
            openapi_request, openapi_response)
        try:
            response_result.raise_for_errors()
        except OpenAPIMappingError as exc:
            errors.extend(response_result.errors)
            return self.handle_openapi_errors(errors)
        else:
            return response

    def handle_openapi_errors(self, errors):
        """Handles OpenAPI request/response errors.
        
        Should return response object::

            class MyOpenAPIView(OpenAPIView):

                def handle_openapi_errors(self, errors):
                    return jsonify({'errors': errors})
        """
        raise NotImplementedError
