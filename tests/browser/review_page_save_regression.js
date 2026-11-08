// Execute this function with Playwright browser_evaluate on a local /page/... URL.
// Requests are simulated: the test never writes to the review database.
async () => {
  const assert = (condition, message) => {
    if (!condition) throw new Error(message);
  };
  const card = document.querySelector('.line-card');
  assert(card, 'Abra uma página com pelo menos uma linha.');
  const editor = card.querySelector('textarea');
  const settle = async () => {
    const deadline = performance.now() + 2000;
    while (editor.readOnly && performance.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 10));
    }
    assert(!editor.readOnly, 'O pedido simulado não terminou em dois segundos.');
  };
  const form = card.querySelector('form');
  const version = card.querySelector('.use-version');
  const submit = card.querySelector('[value="corrected"]');
  const originalText = editor.value;
  const originalStatus = card.dataset.status;
  const originalFetch = window.fetch;
  let requestCount = 0;
  let resolveRequest, rejectRequest;
  const wouldWarn = () => {
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  };
  const edit = text => {
    editor.value = text;
    editor.dispatchEvent(new Event('input', { bubbles: true }));
  };
  const reply = text => resolveRequest(new Response(JSON.stringify({
    line_id: Number(card.dataset.line), status: originalStatus, reviewed_text: text,
  }), { headers: { 'Content-Type': 'application/json' } }));

  window.fetch = () => {
    requestCount++;
    return new Promise((resolve, reject) => {
      resolveRequest = resolve;
      rejectRequest = reject;
    });
  };
  try {
    assert(version, 'A fixture precisa de pelo menos uma versão OCR.');
    submit.click();
    assert(wouldWarn(), 'Sair durante um salvamento sem edição deve avisar.');
    assert(editor.readOnly && version.disabled, 'Editor e versões devem ficar bloqueados.');
    version.click();
    version.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    form.dispatchEvent(new SubmitEvent('submit', { bubbles: true, cancelable: true, submitter: submit }));
    assert(requestCount === 1, 'Um segundo submit não pode iniciar outro pedido.');
    assert(editor.value === originalText, 'A escolha de versão não pode alterar um envio em andamento.');
    reply(originalText);
    await settle();
    assert(!editor.readOnly && !version.disabled && !submit.disabled, 'Controles devem voltar após sucesso.');
    assert(!wouldWarn(), 'Revisão salva não deve continuar pendente.');

    const correction = 'Correção preservada após falha de rede';
    edit(correction);
    submit.click();
    rejectRequest(new TypeError('Falha de rede simulada'));
    await settle();
    assert(editor.value === correction, 'Falha de rede deve preservar a correção.');
    assert(wouldWarn(), 'Correção não salva deve continuar protegida.');
    assert(!editor.readOnly && !version.disabled && !submit.disabled, 'Controles devem voltar após erro.');
    assert(card.querySelector('.save-message').classList.contains('error'), 'A falha deve ficar visível.');

    edit(originalText);
    version.click();
    assert(editor.value === version.closest('.version').querySelector('p').textContent,
      'Escolha de versão deve voltar a funcionar após falha.');
    edit(originalText);
    submit.click();
    reply(originalText);
    await settle();
    assert(!wouldWarn(), 'Retry concluído deve limpar o estado pendente.');
    return { passed: true, simulatedRequests: requestCount, databaseWrites: 0 };
  } finally {
    window.fetch = originalFetch;
  }
}
