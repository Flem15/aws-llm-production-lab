"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.requestObservability = requestObservability;
const node_crypto_1 = require("node:crypto");
function writeStructuredLog(level, event, fields) {
    const record = {
        timestamp: new Date().toISOString(),
        level,
        event,
        service: 'demo-inference-api',
        version: process.env.APP_VERSION ?? 'unknown',
        ...fields,
    };
    console.log(JSON.stringify(record));
}
function requestObservability(request, response, next) {
    const incomingRequestId = request.get('x-request-id')?.trim();
    const requestId = incomingRequestId || (0, node_crypto_1.randomUUID)();
    const startTime = process.hrtime.bigint();
    response.setHeader('x-request-id', requestId);
    writeStructuredLog('info', 'http_request_started', {
        requestId,
        method: request.method,
        path: request.path,
    });
    response.on('finish', () => {
        const elapsedNanoseconds = process.hrtime.bigint()
            - startTime;
        const durationMs = Number(elapsedNanoseconds) / 1000000;
        const level = response.statusCode >= 500
            ? 'error'
            : response.statusCode >= 400
                ? 'warn'
                : 'info';
        writeStructuredLog(level, 'http_request_completed', {
            requestId,
            method: request.method,
            path: request.path,
            statusCode: response.statusCode,
            durationMs: Number(durationMs.toFixed(2)),
        });
    });
    next();
}
