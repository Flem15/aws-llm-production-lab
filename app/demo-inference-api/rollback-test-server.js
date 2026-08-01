const http = require('http');

const port = Number(process.env.PORT ?? 3000);
const version = process.env.APP_VERSION ?? 'rollback-test';

const server = http.createServer((request, response) => {
  if (request.url === '/health') {
    response.writeHead(503, {
      'Content-Type': 'application/json',
    });

    response.end(
      JSON.stringify({
        status: 'unhealthy',
        version,
        test: 'day7-controlled-rollback',
      }),
    );

    return;
  }

  if (request.url === '/invoke' && request.method === 'POST') {
    response.writeHead(503, {
      'Content-Type': 'application/json',
    });

    response.end(
      JSON.stringify({
        error: 'Controlled rollback test image',
        version,
      }),
    );

    return;
  }

  response.writeHead(404, {
    'Content-Type': 'application/json',
  });

  response.end(
    JSON.stringify({
      error: 'not found',
      version,
    }),
  );
});

server.listen(port, '0.0.0.0', () => {
  console.log(
    `Controlled unhealthy rollback-test server listening on ${port}`,
  );
});
