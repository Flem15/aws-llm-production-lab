import express from "express";
import { requestObservability } from './request-observability';

const app = express();
app.use(express.json());

app.use(requestObservability);

const port = process.env.PORT || 3000;
const version = process.env.APP_VERSION || "v1";

app.get("/health", (_req, res) => {
  res.status(200).json({
    status: "ok",
    version
  });
});

app.post("/invoke", (req, res) => {
  const prompt = req.body?.prompt ?? "empty";
  res.status(200).json({
    version,
    prompt,
    response: `demo-response-for: ${prompt}`
  });
});

app.listen(port, () => {
  console.log(`demo inference api listening on ${port}`);
});
