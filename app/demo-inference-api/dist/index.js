"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
const express_1 = __importDefault(require("express"));
const app = (0, express_1.default)();
app.use(express_1.default.json());
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
