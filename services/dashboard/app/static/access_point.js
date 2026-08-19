const ssid = document.querySelector("#ap-ssid"), password = document.querySelector("#ap-password"), reveal = document.querySelector("#ap-reveal"), message = document.querySelector("#ap-message"), qr = document.querySelector("#ap-qr");

async function request(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw Error(payload.detail || "アクセスポイント設定を取得できません");
  return payload;
}

request("/api/admin/access-point").then(data => { ssid.textContent = data.ssid || "未設定"; }).catch(error => { ssid.textContent = "取得できません"; message.textContent = error.message; });
reveal.addEventListener("click", async () => {
  reveal.disabled = true;
  try {
    const data = await request("/api/admin/access-point/reveal", {method: "POST"});
    ssid.textContent = data.ssid;
    password.textContent = data.password;
    qr.innerHTML = data.qr_svg;
    qr.hidden = false;
    message.textContent = "スマートフォンでQRコードを読み取るか、SSIDとパスワードを入力してください。";
    reveal.hidden = true;
  } catch (error) {
    message.textContent = error.message;
    reveal.disabled = false;
  }
});
