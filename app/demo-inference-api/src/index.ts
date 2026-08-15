import express from 'express';
import {
  requestObservability,
} from './request-observability';
import {
  enforceInvokeJsonContentType,
  InvokeRequestBody,
  JSON_BODY_LIMIT,
  jsonBodyErrorHandler,
  notFoundHandler,
  unhandledErrorHandler,
  validateInvokeRequest,
} from './request-validation';

const app = express();

app.disable(
  'x-powered-by',
);

app.use(
  requestObservability,
);

app.use(
  enforceInvokeJsonContentType,
);

app.use(
  express.json({
    limit:
      JSON_BODY_LIMIT,
    strict:
      true,
  }),
);

app.use(
  jsonBodyErrorHandler,
);

app.get(
  '/health',
  (
    _request,
    response,
  ) => {
    response.json({
      status:
        'ok',
      version:
        process.env
          .APP_VERSION ??
        'unknown',
    });
  },
);

app.post(
  '/invoke',
  validateInvokeRequest,
  (
    request,
    response,
  ) => {
    const {
      prompt,
    } =
      request.body as
        InvokeRequestBody;

    const version =
      process.env
        .APP_VERSION ??
      'unknown';

    response.json({
      version,
      prompt,
      response:
        `demo-response-for: ${prompt}`,
    });
  },
);

app.use(
  notFoundHandler,
);

app.use(
  unhandledErrorHandler,
);

const port =
  Number(
    process.env.PORT ??
    '3000',
  );

app.listen(
  port,
  '0.0.0.0',
  () => {
    console.log(
      JSON.stringify({
        timestamp:
          new Date().toISOString(),
        level:
          'info',
        event:
          'application_started',
        service:
          'demo-inference-api',
        version:
          process.env
            .APP_VERSION ??
          'unknown',
        port,
      }),
    );
  },
);
