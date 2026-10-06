document.querySelectorAll('form').forEach(form => {
  form.addEventListener('submit', () => {
    form.querySelectorAll('button').forEach(button => { button.disabled = true; });
    document.getElementById('status').textContent = 'Working… Please wait for Spotify.';
  });
});
window.addEventListener('pageshow', event => { if (event.persisted) window.location.reload(); });
