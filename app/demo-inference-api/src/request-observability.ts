import { randomUUID } from 'node:crypto';
import {
  NextFunction,
  Request,
  Response,
} from 'express';

interface StructuredLogFields {
  readonly [key: string]:
    | string
    | number
    | boolean
    | undefined;
}

function writeStructuredLog(
  level: 'info' | 'warn' | 'error',
  event: string,
  fields: StructuredLogFields,
): void {
  const record = {
    timestamp: new Date().toISOString(),
    level,
    event,
    service: 'demo-inference-api',
    version:
      process.env.APP_VERSION ?? 'unknown',
    ...fields,
  };

  console.log(
    JSON.stringify(record),
  );
}

export function requestObservability(
  request: Request,
  response: Response,
  next: NextFunction,
): void {
  const incomingRequestId =
    request.get('x-request-id')?.trim();

  const requestId =
    incomingRequestId || randomUUID();

  const startTime =
    process.hrtime.bigint();

  response.setHeader(
    'x-request-id',
    requestId,
  );

  writeStructuredLog(
    'info',
    'http_request_started',
    {
      requestId,
      method: request.method,
      path: request.path,
    },
  );

  response.on(
    'finish',
    () => {
      const elapsedNanoseconds =
        process.hrtime.bigint()
        - startTime;

      const durationMs =
        Number(
          elapsedNanoseconds,
        ) / 1_000_000;

      const level =
        response.statusCode >= 500
          ? 'error'
          : response.statusCode >= 400
            ? 'warn'
            : 'info';

      writeStructuredLog(
        level,
        'http_request_completed',
        {
          requestId,
          method: request.method,
          path: request.path,
          statusCode:
            response.statusCode,
          durationMs:
            Number(
              durationMs.toFixed(2),
            ),
        },
      );
    },
  );

  next();
}
