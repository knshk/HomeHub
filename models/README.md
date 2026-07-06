# Drop-in models folder

Put GGUF model files (`*.gguf`) here, then open the **Models** tab in the Home Hub
and click **Scan**. Each new `.gguf` is imported into Ollama automatically
(`ollama create`) and registered in the dashboard, where you can Start / Suspend
/ Shutdown it like any other model.

Notes
- The gateway watches this folder (path: `MODELS_DIR`, default this directory).
- A bare GGUF imports as a **chat** model. Vision/embedding GGUFs may need a
  custom template — set the role when you Add, or edit the generated Modelfile.
- Models pulled with `ollama pull ...` are also auto-registered on the next
  dashboard refresh (no file needed here).
