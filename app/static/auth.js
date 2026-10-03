/**
 * Shared authentication helpers for all SalonAutoZone pages.
 * Loaded by every page that needs auth. Do not duplicate this code.
 */
window.Auth = (function () {
    var TOKEN_KEYS = ['token', 'access_token', 'authToken', 'jwt', 'userToken'];

    function getToken() {
        try {
            for (var i = 0; i < TOKEN_KEYS.length; i++) {
                var v = localStorage.getItem(TOKEN_KEYS[i]);
                if (v) return v;
            }
        } catch (e) {}
        return '';
    }

    function getUser() {
        try {
            return JSON.parse(localStorage.getItem('userInfo') || 'null') || {};
        } catch (e) {
            return {};
        }
    }

    function clearSession() {
        try {
            TOKEN_KEYS.concat(['userInfo']).forEach(function (k) {
                localStorage.removeItem(k);
            });
        } catch (e) {}
    }

    function goLogin(reason) {
        try { console.warn('AUTH REDIRECT:', reason); } catch (e) {}
        clearSession();
        var next = encodeURIComponent(location.pathname + location.search);
        location.replace('/login?next=' + next);
    }

    function requireLogin() {
        if (!getToken()) {
            goLogin('no token');
            return false;
        }
        return true;
    }

    return {
        TOKEN_KEYS: TOKEN_KEYS,
        getToken: getToken,
        getUser: getUser,
        clearSession: clearSession,
        goLogin: goLogin,
        requireLogin: requireLogin
    };
})();
