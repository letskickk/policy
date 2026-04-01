(() => {
  const FOOTER_HTML = `
    <div class="shared-footer-shell">
      <footer class="site-footer">
        <div class="footer-left">
          <span class="footer-org">개혁신당 정책국</span>
          <span class="footer-dot">·</span>
          <span>AI 정책 플랫폼</span>
        </div>
        <div class="footer-right">
          <button type="button" data-contact-open>✉ 문의하기</button>
        </div>
      </footer>
    </div>
    <div class="contact-overlay" data-contact-overlay aria-hidden="true" style="display:none">
      <div class="contact-modal" role="dialog" aria-modal="true" aria-labelledby="contactModalTitle">
        <div data-contact-form>
          <h3 id="contactModalTitle">문의하기</h3>
          <p class="sub">개혁신당 정책국에 문의 사항을 보내주세요.</p>
          <label for="sharedContactName">이름</label>
          <input type="text" id="sharedContactName" data-contact-name placeholder="이름" maxlength="100">
          <label for="sharedContactEmail">이메일 <span style="color:#f87171">*</span></label>
          <input type="email" id="sharedContactEmail" data-contact-email placeholder="답변 받으실 이메일" maxlength="200">
          <label for="sharedContactMsg">문의 내용 <span style="color:#f87171">*</span></label>
          <textarea id="sharedContactMsg" data-contact-message placeholder="문의 내용을 입력해 주세요." maxlength="5000" rows="4"></textarea>
          <p class="contact-err" data-contact-error></p>
          <div class="contact-actions">
            <button type="button" class="contact-btn-cancel" data-contact-close>취소</button>
            <button type="button" class="contact-btn-send" data-contact-send>보내기</button>
          </div>
        </div>
        <div class="contact-ok" data-contact-success hidden>
          문의가 전송되었습니다.<br>빠른 시일 내 답변드리겠습니다.
          <div style="margin-top:1rem;">
            <button type="button" class="contact-btn-cancel" data-contact-close>닫기</button>
          </div>
        </div>
      </div>
    </div>
  `;

  function mountFooter(slot) {
    if (!slot || slot.dataset.footerMounted === "true") return;

    slot.innerHTML = FOOTER_HTML;
    slot.dataset.footerMounted = "true";

    const overlay = slot.querySelector("[data-contact-overlay]");
    const openButton = slot.querySelector("[data-contact-open]");
    const closeButtons = slot.querySelectorAll("[data-contact-close]");
    const formWrap = slot.querySelector("[data-contact-form]");
    const successWrap = slot.querySelector("[data-contact-success]");
    const nameInput = slot.querySelector("[data-contact-name]");
    const emailInput = slot.querySelector("[data-contact-email]");
    const messageInput = slot.querySelector("[data-contact-message]");
    const errorEl = slot.querySelector("[data-contact-error]");
    const sendButton = slot.querySelector("[data-contact-send]");

    const resetState = () => {
      formWrap.hidden = false;
      successWrap.hidden = true;
      errorEl.style.display = "none";
      errorEl.textContent = "";
      sendButton.disabled = false;
      sendButton.textContent = "보내기";

      if (window._userEmail && !emailInput.value) emailInput.value = window._userEmail;
      if (window._userName && !nameInput.value) nameInput.value = window._userName;
    };

    const openModal = () => {
      resetState();
      if (window.PolicyModal) {
        window.PolicyModal.open(overlay, {
          container: ".contact-modal",
          initialFocus: "[data-contact-email]",
          onEscape: closeModal,
          display: "flex",
          restoreFocusTo: openButton,
        });
      } else {
        overlay.style.display = "flex";
        overlay.classList.add("open");
        document.body.style.overflow = "hidden";
      }
    };

    const closeModal = () => {
      if (window.PolicyModal) {
        window.PolicyModal.close(overlay, { display: "none" });
      } else {
        overlay.classList.remove("open");
        overlay.style.display = "none";
        document.body.style.overflow = "";
      }
    };

    const sendContact = async () => {
      const email = emailInput.value.trim();
      const message = messageInput.value.trim();
      const name = nameInput.value.trim();

      if (!email || !message) {
        errorEl.textContent = "이메일과 문의 내용을 입력해 주세요.";
        errorEl.style.display = "block";
        return;
      }

      sendButton.disabled = true;
      sendButton.textContent = "전송 중...";
      errorEl.style.display = "none";

      try {
        const response = await fetch("/api/contact", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ name, email, message }),
        });

        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          throw new Error(data.detail || "전송에 실패했습니다.");
        }

        formWrap.hidden = true;
        successWrap.hidden = false;
        requestAnimationFrame(() => {
          const successClose = successWrap.querySelector("[data-contact-close]");
          if (successClose) successClose.focus();
        });
        nameInput.value = "";
        emailInput.value = "";
        messageInput.value = "";
      } catch (error) {
        errorEl.textContent = error.message || "전송에 실패했습니다.";
        errorEl.style.display = "block";
      } finally {
        sendButton.disabled = false;
        sendButton.textContent = "보내기";
      }
    };

    openButton.addEventListener("click", openModal);
    closeButtons.forEach((button) => button.addEventListener("click", closeModal));
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) closeModal();
    });
    sendButton.addEventListener("click", sendContact);

    slot.openContactModal = openModal;
    slot.closeContactModal = closeModal;
    slot.sendContact = sendContact;
  }

  function initSharedFooter() {
    document.querySelectorAll("[data-shared-footer]").forEach(mountFooter);
  }

  window.openContactModal = () => {
    const slot = document.querySelector("[data-shared-footer]");
    if (slot && typeof slot.openContactModal === "function") slot.openContactModal();
  };

  window.closeContactModal = () => {
    const slot = document.querySelector("[data-shared-footer]");
    if (slot && typeof slot.closeContactModal === "function") slot.closeContactModal();
  };

  window.sendContact = () => {
    const slot = document.querySelector("[data-shared-footer]");
    if (slot && typeof slot.sendContact === "function") return slot.sendContact();
    return Promise.resolve();
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initSharedFooter, { once: true });
  } else {
    initSharedFooter();
  }
})();
