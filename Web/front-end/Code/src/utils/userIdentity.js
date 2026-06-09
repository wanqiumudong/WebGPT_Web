import Cookies from 'js-cookie';

export const normalizeIdentity = (value) => {
  if (value === null || value === undefined) return '';
  const text = String(value).trim();
  if (!text) return '';
  const lowered = text.toLowerCase();
  if (lowered === 'undefined' || lowered === 'null') return '';
  return text;
};

export const resolveCurrentUserId = ({
  preferredUserId = '',
  preferredUsername = '',
} = {}) => {
  const username =
    normalizeIdentity(preferredUsername) ||
    normalizeIdentity(Cookies.get('user'));
  if (username) {
    try {
      localStorage.setItem('currentUserId', username);
    } catch (error) {
      // Ignore storage failures in restricted browser contexts.
    }
    return username;
  }

  const explicitUserId = normalizeIdentity(preferredUserId);
  if (explicitUserId) return explicitUserId;

  try {
    const storedUserId = normalizeIdentity(localStorage.getItem('currentUserId'));
    if (storedUserId) return storedUserId;
  } catch (error) {
    // Ignore storage failures in restricted browser contexts.
  }

  const cookieUserId =
    normalizeIdentity(Cookies.get('userid')) ||
    normalizeIdentity(Cookies.get('userId'));
  if (cookieUserId) return cookieUserId;

  if (typeof window !== 'undefined') {
    const urlParams = new URLSearchParams(window.location.search);
    const urlUserId = normalizeIdentity(
      urlParams.get('userId') || urlParams.get('user_id'),
    );
    if (urlUserId) return urlUserId;
  }

  return 'default';
};
