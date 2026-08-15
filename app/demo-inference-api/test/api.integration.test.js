const assert =
  require('node:assert/strict');

const {
  spawn,
} = require(
  'node:child_process',
);

const {
  after,
  before,
  test,
} = require(
  'node:test',
);

const port = 3100;

const expectedVersion =
  'day12-integration-test';

const baseUrl =
  `http://127.0.0.1:${port}`;

let serverProcess;
let capturedOutput = '';

async function sleep(
  milliseconds,
) {
  await new Promise(
    (resolve) => {
      setTimeout(
        resolve,
        milliseconds,
      );
    },
  );
}

async function waitForHealth({
  attempts = 30,
  delayMilliseconds = 500,
} = {}) {
  let lastError;

  for (
    let attempt = 1;
    attempt <= attempts;
    attempt += 1
  ) {
    try {
      const response =
        await fetch(
          `${baseUrl}/health`,
        );

      if (response.ok) {
        return;
      }

      lastError =
        new Error(
          `Health endpoint returned HTTP ${response.status}`,
        );
    } catch (error) {
      lastError =
        error;
    }

    await sleep(
      delayMilliseconds,
    );
  }

  throw new Error(
    `Application did not become healthy. Last error: ${
      lastError?.message ??
      'unknown'
    }\nCaptured application output:\n${capturedOutput}`,
  );
}

function assertErrorResponse({
  response,
  body,
  status,
  code,
  requestId,
}) {
  assert.equal(
    response.status,
    status,
  );

  assert.equal(
    body.error?.code,
    code,
  );

  assert.equal(
    typeof body.error?.message,
    'string',
  );

  assert.ok(
    body.error.message.length > 0,
  );

  assert.equal(
    body.error?.requestId,
    requestId,
  );

  assert.equal(
    response.headers.get(
      'x-request-id',
    ),
    requestId,
  );
}

before(
  async () => {
    serverProcess =
      spawn(
        'npm',
        [
          'start',
        ],
        {
          cwd:
            process.cwd(),
          env: {
            ...process.env,
            PORT:
              String(port),
            APP_VERSION:
              expectedVersion,
          },
          stdio: [
            'ignore',
            'pipe',
            'pipe',
          ],
        },
      );

    serverProcess.stdout.on(
      'data',
      (chunk) => {
        capturedOutput +=
          chunk.toString();
      },
    );

    serverProcess.stderr.on(
      'data',
      (chunk) => {
        capturedOutput +=
          chunk.toString();
      },
    );

    await waitForHealth();
  },
);

after(
  async () => {
    if (
      !serverProcess ||
      serverProcess.killed
    ) {
      return;
    }

    serverProcess.kill(
      'SIGTERM',
    );

    await Promise.race([
      new Promise(
        (resolve) => {
          serverProcess.once(
            'exit',
            resolve,
          );
        },
      ),
      sleep(3000),
    ]);

    if (
      !serverProcess.killed
    ) {
      serverProcess.kill(
        'SIGKILL',
      );
    }
  },
);

test(
  'GET /health returns the configured version',
  async () => {
    const response =
      await fetch(
        `${baseUrl}/health`,
      );

    const body =
      await response.json();

    assert.equal(
      response.status,
      200,
    );

    assert.deepEqual(
      body,
      {
        status:
          'ok',
        version:
          expectedVersion,
      },
    );
  },
);

test(
  'request correlation ID is preserved',
  async () => {
    const requestId =
      'day12-correlation-test';

    const response =
      await fetch(
        `${baseUrl}/health`,
        {
          headers: {
            'x-request-id':
              requestId,
          },
        },
      );

    assert.equal(
      response.status,
      200,
    );

    assert.equal(
      response.headers.get(
        'x-request-id',
      ),
      requestId,
    );
  },
);

test(
  'valid POST /invoke succeeds',
  async () => {
    const prompt =
      'hello from Day 12';

    const response =
      await fetch(
        `${baseUrl}/invoke`,
        {
          method:
            'POST',
          headers: {
            'Content-Type':
              'application/json',
          },
          body:
            JSON.stringify({
              prompt,
            }),
        },
      );

    const body =
      await response.json();

    assert.equal(
      response.status,
      200,
    );

    assert.equal(
      body.version,
      expectedVersion,
    );

    assert.equal(
      body.prompt,
      prompt,
    );

    assert.equal(
      body.response,
      `demo-response-for: ${prompt}`,
    );
  },
);

test(
  'application/json with charset is accepted',
  async () => {
    const response =
      await fetch(
        `${baseUrl}/invoke`,
        {
          method:
            'POST',
          headers: {
            'Content-Type':
              'application/json; charset=utf-8',
          },
          body:
            JSON.stringify({
              prompt:
                'charset test',
            }),
        },
      );

    assert.equal(
      response.status,
      200,
    );
  },
);

test(
  'missing JSON Content-Type is rejected',
  async () => {
    const requestId =
      'day12-content-type';

    const response =
      await fetch(
        `${baseUrl}/invoke`,
        {
          method:
            'POST',
          headers: {
            'x-request-id':
              requestId,
          },
          body:
            '{"prompt":"test"}',
        },
      );

    const body =
      await response.json();

    assertErrorResponse({
      response,
      body,
      status:
        415,
      code:
        'UNSUPPORTED_MEDIA_TYPE',
      requestId,
    });
  },
);

test(
  'malformed JSON receives structured 400',
  async () => {
    const requestId =
      'day12-malformed-json';

    const response =
      await fetch(
        `${baseUrl}/invoke`,
        {
          method:
            'POST',
          headers: {
            'Content-Type':
              'application/json',
            'x-request-id':
              requestId,
          },
          body:
            '{"prompt":',
        },
      );

    const body =
      await response.json();

    assertErrorResponse({
      response,
      body,
      status:
        400,
      code:
        'MALFORMED_JSON',
      requestId,
    });
  },
);

test(
  'missing prompt fails JSON schema',
  async () => {
    const requestId =
      'day12-missing-prompt';

    const response =
      await fetch(
        `${baseUrl}/invoke`,
        {
          method:
            'POST',
          headers: {
            'Content-Type':
              'application/json',
            'x-request-id':
              requestId,
          },
          body:
            JSON.stringify({}),
        },
      );

    const body =
      await response.json();

    assertErrorResponse({
      response,
      body,
      status:
        400,
      code:
        'INVALID_REQUEST',
      requestId,
    });
  },
);

test(
  'non-string prompt fails JSON schema',
  async () => {
    const requestId =
      'day12-wrong-type';

    const response =
      await fetch(
        `${baseUrl}/invoke`,
        {
          method:
            'POST',
          headers: {
            'Content-Type':
              'application/json',
            'x-request-id':
              requestId,
          },
          body:
            JSON.stringify({
              prompt:
                12345,
            }),
        },
      );

    const body =
      await response.json();

    assertErrorResponse({
      response,
      body,
      status:
        400,
      code:
        'INVALID_REQUEST',
      requestId,
    });
  },
);

test(
  'additional JSON properties are rejected',
  async () => {
    const requestId =
      'day12-extra-property';

    const response =
      await fetch(
        `${baseUrl}/invoke`,
        {
          method:
            'POST',
          headers: {
            'Content-Type':
              'application/json',
            'x-request-id':
              requestId,
          },
          body:
            JSON.stringify({
              prompt:
                'valid',
              unexpected:
                true,
            }),
        },
      );

    const body =
      await response.json();

    assertErrorResponse({
      response,
      body,
      status:
        400,
      code:
        'INVALID_REQUEST',
      requestId,
    });
  },
);

test(
  'blank prompt is rejected',
  async () => {
    const requestId =
      'day12-blank-prompt';

    const response =
      await fetch(
        `${baseUrl}/invoke`,
        {
          method:
            'POST',
          headers: {
            'Content-Type':
              'application/json',
            'x-request-id':
              requestId,
          },
          body:
            JSON.stringify({
              prompt:
                '     ',
            }),
        },
      );

    const body =
      await response.json();

    assertErrorResponse({
      response,
      body,
      status:
        400,
      code:
        'INVALID_REQUEST',
      requestId,
    });
  },
);

test(
  'exactly 4000 prompt characters are accepted',
  async () => {
    const response =
      await fetch(
        `${baseUrl}/invoke`,
        {
          method:
            'POST',
          headers: {
            'Content-Type':
              'application/json',
          },
          body:
            JSON.stringify({
              prompt:
                'a'.repeat(4000),
            }),
        },
      );

    assert.equal(
      response.status,
      200,
    );
  },
);

test(
  'prompt longer than 4000 characters is rejected',
  async () => {
    const requestId =
      'day12-long-prompt';

    const response =
      await fetch(
        `${baseUrl}/invoke`,
        {
          method:
            'POST',
          headers: {
            'Content-Type':
              'application/json',
            'x-request-id':
              requestId,
          },
          body:
            JSON.stringify({
              prompt:
                'a'.repeat(4001),
            }),
        },
      );

    const body =
      await response.json();

    assertErrorResponse({
      response,
      body,
      status:
        400,
      code:
        'PROMPT_TOO_LONG',
      requestId,
    });
  },
);

test(
  'JSON body larger than 16 KB is rejected',
  async () => {
    const requestId =
      'day12-large-body';

    const response =
      await fetch(
        `${baseUrl}/invoke`,
        {
          method:
            'POST',
          headers: {
            'Content-Type':
              'application/json',
            'x-request-id':
              requestId,
          },
          body:
            JSON.stringify({
              prompt:
                'a'.repeat(
                  20 * 1024,
                ),
            }),
        },
      );

    const body =
      await response.json();

    assertErrorResponse({
      response,
      body,
      status:
        413,
      code:
        'PAYLOAD_TOO_LARGE',
      requestId,
    });
  },
);

test(
  'unknown routes return structured 404',
  async () => {
    const requestId =
      'day12-not-found';

    const response =
      await fetch(
        `${baseUrl}/does-not-exist`,
        {
          headers: {
            'x-request-id':
              requestId,
          },
        },
      );

    const body =
      await response.json();

    assertErrorResponse({
      response,
      body,
      status:
        404,
      code:
        'NOT_FOUND',
      requestId,
    });
  },
);
