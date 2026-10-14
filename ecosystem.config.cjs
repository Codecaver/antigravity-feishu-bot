const fs = require('fs');
const path = require('path');

function loadEnvFile(filePath) {
  const out = {};
  if (!fs.existsSync(filePath)) return out;
  for (const line of fs.readFileSync(filePath, 'utf8').split(/\r?\n/)) {
    const s = line.trim();
    if (!s || s.startsWith('#') || !s.includes('=')) continue;
    const i = s.indexOf('=');
    const k = s.slice(0, i).trim();
    let v = s.slice(i + 1).trim();
    if (
      (v.startsWith('"') && v.endsWith('"')) ||
      (v.startsWith("'") && v.endsWith("'"))
    ) {
      v = v.slice(1, -1);
    }
    out[k] = v;
  }
  return out;
}

const hermes = loadEnvFile(path.join(process.env.HOME || '', '.hermes', '.env'));
const cert = path.join(
  __dirname,
  'venv/lib/python3.14/site-packages/certifi/cacert.pem'
);

// agy only reads GEMINI_API_KEY; Hermes stores Google AI Studio key as GOOGLE_API_KEY.
const geminiKey =
  process.env.GEMINI_API_KEY ||
  hermes.GEMINI_API_KEY ||
  hermes.GOOGLE_API_KEY ||
  '';

module.exports = {
  apps: [
    {
      name: 'feishu-bot',
      cwd: __dirname,
      script: 'venv/bin/python3',
      args: 'main.py',
      interpreter: 'none',
      env: {
        SSL_CERT_FILE: cert,
        REQUESTS_CA_BUNDLE: cert,
        CURL_CA_BUNDLE: cert,
        ...(geminiKey ? { GEMINI_API_KEY: geminiKey } : {}),
      },
    },
  ],
};
