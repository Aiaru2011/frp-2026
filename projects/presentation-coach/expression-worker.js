import { pipeline, RawImage } from "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.7.6";

let classifier = null;
let loading = null;

function load() {
  if (classifier) return Promise.resolve(classifier);
  if (loading) return loading;
  loading = pipeline("image-classification", "onnx-community/vit-face-expression-ONNX", {
    dtype: "q8",
    device: "wasm",
    progress_callback: (p) => {
      if (p.status === "progress" && p.total) {
        self.postMessage({ type: "progress", file: p.file, loaded: p.loaded, total: p.total });
      }
    }
  }).then((model) => {
    classifier = model;
    self.postMessage({ type: "ready" });
    return model;
  }).catch((e) => {
    loading = null;
    self.postMessage({ type: "error", error: String((e && e.message) || e) });
    throw e;
  });
  return loading;
}

self.onmessage = async (ev) => {
  const m = ev.data || {};
  if (m.type === "init") {
    try {
      await load();
    } catch (e) {
      // The error was already reported to the page.
    }
    return;
  }
  if (m.type !== "classify") return;
  try {
    const model = await load();
    const image = new RawImage(m.data, m.width, m.height, 4);
    const labels = await model(image, { top_k: 7 });
    self.postMessage({ type: "result", id: m.id, session: m.session, t: m.t, labels });
  } catch (e) {
    self.postMessage({ type: "classify-error", id: m.id, error: String((e && e.message) || e) });
  }
};
