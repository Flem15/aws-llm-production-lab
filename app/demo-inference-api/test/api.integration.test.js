const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { after, before, test } = require('node:test');

const port = 3100;
const expectedVersion = 'day8-integration-test';
const baseUrl = `http://127.0.0.1:${port}`;

let serverProcess;
let capturedOutput = '';

async function sleep(milliseconds) {
  await new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });
}

async function waitForHealth({
  attempts = 30,
  delayMilliseconds = 500,
} = {}) {
  let lastError;

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const response = await fetch(`${baseUrl}/health`);

      if (response.ok) {
        return;
      }

      lastError = new Error(
        `Health endpoint returned HTTP ${response.status}`,
      );
    } catch (error) {
      lastError = error;
    }

    await sleep(delayMilliseconds);
  }

  throw new Error(
    `Application did not become healthy. Last error: ${
      lastError?.message ?? 'unknown'
    }\nCaptured application output:\n${capturedOutput}`,
  );
}

before(async () => {
  serverProcess = spawn(
    process.execPath,
    ['dist/index.js'],
    {
      cwd: process.cwd(),
      env: {
        ...process.env,
        PORT: String(port),
        APP_VERSION: expectedVersion,
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  );

  serverProcess.stdout.on('data', (chunk) => {
    capturedOutput += chunk.toString();
  });

  serverProcess.stderr.on('data', (chunk) => {
    capturedOutput += chunk.toString();
  });

  serverProcess.on('exit', (code, signal) => {
    if (code !== null && code !== 0) {
      capturedOutput +=
        `\nApplication exited early with code ${code}.`;
    }

    if (signal) {
      capturedOutput +=
        `\nApplication exited with signal ${signal}.`;
    }
  });

  await waitForHealth();
});

after(async () => {
  if (
    !serverProcess ||
    serverProcess.exitCode !== null ||
    serverProcess.signalCode !== null
  ) {
    return;
  }

  const waitForExit = (timeoutMilliseconds) =>
    new Promise((resolve) => {
      let settled = false;

      const finish = (exited) => {
        if (settled) {
          return;
        }

        settled = true;
        clearTimeout(timer);
        serverProcess.off('exit', onExit);
        resolve(exited);
      };

      const onExit = () => {
        finish(true);
      };

      const timer = setTimeout(
        () => finish(false),
        timeoutMilliseconds,
      );

      serverProcess.once('exit', onExit);
    });

  serverProcess.kill('SIGTERM');

  const exitedGracefully =
    await waitForExit(3000);

  if (!exitedGracefully) {
    serverProcess.kill('SIGKILL');

    const exitedAfterKill =
      await waitForExit(3000);

    if (!exitedAfterKill) {
      throw new Error(
        'Application process did not exit after SIGKILL',
      );
    }
  }
});

test('GET /health returns the configured application version', async () => {
  const response = await fetch(`${baseUrl}/health`);
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.deepEqual(body, {
    status: 'ok',
    version: expectedVersion,
  });
});

test('POST /invoke returns the prompt and demo response', async () => {
  const prompt = 'hello from the Day 8 integration test';

  const response = await fetch(`${baseUrl}/invoke`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      prompt,
    }),
  });

  const body = await response.json();

  assert.equal(response.status, 200);
  assert.equal(body.version, expectedVersion);
  assert.equal(body.prompt, prompt);
  assert.equal(
    body.response,
    `demo-response-for: ${prompt}`,
  );
});

test('unknown paths return HTTP 404', async () => {
  const response = await fetch(`${baseUrl}/does-not-exist`);

  assert.equal(response.status, 404);
});

test(
  'request correlation ID is preserved',
  async () => {
    const requestId =
      'day11-correlation-test';

    const response = await fetch(
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
