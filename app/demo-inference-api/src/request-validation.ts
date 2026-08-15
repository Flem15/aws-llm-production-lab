import Ajv from 'ajv';
import {
  ErrorRequestHandler,
  NextFunction,
  Request,
  RequestHandler,
  Response,
} from 'express';

export const MAX_PROMPT_LENGTH = 4000;
export const JSON_BODY_LIMIT = '16kb';

export interface InvokeRequestBody {
  readonly prompt: string;
}

export interface ApiErrorBody {
  readonly error: {
    readonly code: string;
    readonly message: string;
    readonly requestId: string;
  };
}

const ajv = new Ajv({
  allErrors: true,
  strict: true,
});

const invokeRequestSchema = {
  type: 'object',
  additionalProperties: false,
  required: [
    'prompt',
  ],
  properties: {
    prompt: {
      type: 'string',
      minLength: 1,
      maxLength:
        MAX_PROMPT_LENGTH,
    },
  },
} as const;

const validateInvokeSchema =
  ajv.compile(
    invokeRequestSchema,
  );

function getRequestId(
  response: Response,
): string {
  const headerValue =
    response.getHeader(
      'x-request-id',
    );

  if (
    typeof headerValue ===
      'string' &&
    headerValue.length > 0
  ) {
    return headerValue;
  }

  return 'unknown';
}

function sendApiError(
  response: Response,
  statusCode: number,
  code: string,
  message: string,
): Response<ApiErrorBody> {
  return response
    .status(statusCode)
    .json({
      error: {
        code,
        message,
        requestId:
          getRequestId(response),
      },
    });
}

export const enforceInvokeJsonContentType:
  RequestHandler = (
    request,
    response,
    next,
  ) => {
    if (
      request.method !== 'POST' ||
      request.path !== '/invoke'
    ) {
      next();
      return;
    }

    const contentType =
      request
        .get('content-type')
        ?.split(';')[0]
        ?.trim()
        .toLowerCase();

    if (
      contentType !==
      'application/json'
    ) {
      sendApiError(
        response,
        415,
        'UNSUPPORTED_MEDIA_TYPE',
        'Content-Type must be application/json.',
      );

      return;
    }

    next();
  };

export const validateInvokeRequest:
  RequestHandler = (
    request,
    response,
    next,
  ) => {
    const body =
      request.body as
        | Partial<
            InvokeRequestBody
          >
        | undefined;

    if (
      typeof body?.prompt ===
        'string' &&
      body.prompt.length >
        MAX_PROMPT_LENGTH
    ) {
      sendApiError(
        response,
        400,
        'PROMPT_TOO_LONG',
        `prompt must not exceed ${MAX_PROMPT_LENGTH} characters.`,
      );

      return;
    }

    if (
      !validateInvokeSchema(
        request.body,
      )
    ) {
      sendApiError(
        response,
        400,
        'INVALID_REQUEST',
        'Request body must contain only a non-empty string prompt.',
      );

      return;
    }

    const prompt =
      (
        request.body as
          InvokeRequestBody
      ).prompt;

    if (
      prompt.trim().length === 0
    ) {
      sendApiError(
        response,
        400,
        'INVALID_REQUEST',
        'prompt must contain at least one non-whitespace character.',
      );

      return;
    }

    next();
  };

export const jsonBodyErrorHandler:
  ErrorRequestHandler = (
    error,
    _request,
    response,
    next,
  ) => {
    const bodyParserError =
      error as {
        readonly type?: string;
        readonly status?: number;
      };

    if (
      bodyParserError.type ===
        'entity.too.large' ||
      bodyParserError.status ===
        413
    ) {
      sendApiError(
        response,
        413,
        'PAYLOAD_TOO_LARGE',
        `JSON request body must not exceed ${JSON_BODY_LIMIT}.`,
      );

      return;
    }

    if (
      bodyParserError.type ===
        'entity.parse.failed' ||
      (
        error instanceof
          SyntaxError &&
        bodyParserError.status ===
          400
      )
    ) {
      sendApiError(
        response,
        400,
        'MALFORMED_JSON',
        'Request body contains malformed JSON.',
      );

      return;
    }

    next(error);
  };

export const notFoundHandler:
  RequestHandler = (
    _request,
    response,
  ) => {
    sendApiError(
      response,
      404,
      'NOT_FOUND',
      'The requested resource was not found.',
    );
  };

export const unhandledErrorHandler:
  ErrorRequestHandler = (
    error,
    request,
    response,
    _next,
  ) => {
    console.error(
      JSON.stringify({
        timestamp:
          new Date().toISOString(),
        level:
          'error',
        event:
          'unhandled_application_error',
        service:
          'demo-inference-api',
        version:
          process.env
            .APP_VERSION ??
          'unknown',
        requestId:
          getRequestId(
            response,
          ),
        method:
          request.method,
        path:
          request.path,
        errorType:
          error instanceof Error
            ? error.name
            : 'UnknownError',
      }),
    );

    if (response.headersSent) {
      return;
    }

    sendApiError(
      response,
      500,
      'INTERNAL_ERROR',
      'An internal server error occurred.',
    );
  };
