const express = require('express');
const path = require('path');
const cookieParser = require('cookie-parser');
const crypto = require('crypto');
const axios = require('axios');

const app = express();
const PORT = process.env.PORT || 3000;
const API_URL = process.env.API_URL || 'http://api:8000';

app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, 'views'));
app.use(express.static(path.join(__dirname, 'public')));
app.use(express.urlencoded({ extended: true }));
app.use(express.json());
app.use(cookieParser());

function csrfToken(req, res, next) {
  if (!req.cookies.csrf_token) {
    const token = crypto.randomBytes(32).toString('hex');
    res.cookie('csrf_token', token, {
      httpOnly: false,
      sameSite: 'lax',
      secure: process.env.APP_ENV === 'production',
      maxAge: 3600000,
    });
    req.csrfToken = token;
  } else {
    req.csrfToken = req.cookies.csrf_token;
  }
  next();
}

function csrfProtect(req, res, next) {
  const token = req.body._csrf || req.headers['x-csrf-token'];
  if (!token || token !== req.cookies.csrf_token) {
    return res.status(403).render('error', {
      title: 'Forbidden',
      message: 'Invalid CSRF token',
      user: null,
      csrfToken: req.csrfToken,
    });
  }
  next();
}

app.use(csrfToken);

function apiHeaders(req) {
  const headers = {};
  if (req.cookies.access_token) {
    headers['Authorization'] = `Bearer ${req.cookies.access_token}`;
  }
  return headers;
}

async function refreshTokens(req, res) {
  const refreshToken = req.cookies.refresh_token;
  if (!refreshToken) return false;

  try {
    const resp = await axios.post(`${API_URL}/api/v1/auth/refresh`, {
      refresh_token: refreshToken,
    });

    if (resp.status === 200 && resp.data.access_token) {
      res.cookie('access_token', resp.data.access_token, {
        httpOnly: true,
        sameSite: 'lax',
        secure: process.env.APP_ENV === 'production',
        maxAge: 900000,
        path: '/',
      });
      res.cookie('refresh_token', resp.data.refresh_token, {
        httpOnly: true,
        sameSite: 'lax',
        secure: process.env.APP_ENV === 'production',
        maxAge: 604800000,
        path: '/',
      });
      req.cookies.access_token = resp.data.access_token;
      return true;
    }
  } catch (e) { /* refresh failed */ }
  return false;
}

async function apiRequest(req, res, method, path, data = null) {
  try {
    const config = { method, url: `${API_URL}${path}`, headers: apiHeaders(req) };
    if (data) config.data = data;
    return await axios(config);
  } catch (err) {
    if (err.response && err.response.status === 401) {
      const refreshed = await refreshTokens(req, res);
      if (refreshed) {
        const config = { method, url: `${API_URL}${path}`, headers: apiHeaders(req) };
        if (data) config.data = data;
        return await axios(config);
      }
    }
    throw err;
  }
}

async function getCurrentUser(req, res) {
  if (!req.cookies.access_token) return null;
  try {
    const resp = await apiRequest(req, res, 'get', '/api/v1/auth/me');
    return resp.data;
  } catch {
    return null;
  }
}

function requireAuth(req, res, next) {
  if (!req.cookies.access_token) {
    return res.redirect('/login');
  }
  next();
}

/* ── Health ── */
app.get('/health', (req, res) => {
  res.json({ message: 'healthy' });
});

/* ── Stage 2 backward-compatibility routes ── */
app.post('/submit', async (req, res) => {
  try {
    const resp = await axios.post(`${API_URL}/jobs`);
    res.json(resp.data);
  } catch {
    res.status(500).json({ error: 'Failed to submit job' });
  }
});

app.get('/status/:id', async (req, res) => {
  try {
    const resp = await axios.get(`${API_URL}/jobs/${req.params.id}`);
    res.json(resp.data);
  } catch {
    res.status(500).json({ error: 'Failed to get status' });
  }
});

/* ── Auth Pages ── */
app.get('/login', async (req, res) => {
  const user = await getCurrentUser(req, res);
  if (user) return res.redirect('/dashboard');
  res.render('login', { title: 'Login', user: null, csrfToken: req.csrfToken });
});

app.get('/auth/github', (req, res) => {
  res.redirect(`${API_URL}/api/v1/auth/github?interface=web`);
});

app.get('/logout', requireAuth, async (req, res) => {
  try {
    await apiRequest(req, res, 'post', '/api/v1/auth/logout');
  } catch { /* ignore */ }
  res.clearCookie('access_token');
  res.clearCookie('refresh_token');
  res.clearCookie('csrf_token');
  res.redirect('/login');
});

/* ── Dashboard ── */
app.get('/dashboard', requireAuth, async (req, res) => {
  const user = await getCurrentUser(req, res);
  if (!user) return res.redirect('/login');

  let stats = { totalProfiles: 0, recentProfiles: [] };
  try {
    const resp = await apiRequest(req, res, 'get', '/api/v1/profiles?per_page=5&sort_by=created_at&order=desc');
    stats.totalProfiles = resp.data.meta.total;
    stats.recentProfiles = resp.data.data;
  } catch { /* ignore */ }

  res.render('dashboard', { title: 'Dashboard', user, stats, csrfToken: req.csrfToken });
});

/* ── Profiles ── */
app.get('/profiles', requireAuth, async (req, res) => {
  const user = await getCurrentUser(req, res);
  if (!user) return res.redirect('/login');

  const { page = 1, per_page = 20, sort_by = 'created_at', order = 'desc', q, location, skills, company, search } = req.query;
  const params = new URLSearchParams({ page, per_page, sort_by, order });
  if (q) params.append('q', q);
  if (location) params.append('location', location);
  if (skills) params.append('skills', skills);
  if (company) params.append('company', company);
  if (search) params.append('search', search);

  let result = { data: [], meta: { page: 1, per_page: 20, total: 0, total_pages: 1, has_next: false, has_prev: false } };
  try {
    const resp = await apiRequest(req, res, 'get', `/api/v1/profiles?${params.toString()}`);
    result = resp.data;
  } catch { /* ignore */ }

  res.render('profiles', { title: 'Profiles', user, profiles: result.data, meta: result.meta, query: req.query, csrfToken: req.csrfToken });
});

app.get('/profiles/export', requireAuth, async (req, res) => {
  const params = new URLSearchParams();
  if (req.query.q) params.append('q', req.query.q);
  if (req.query.location) params.append('location', req.query.location);
  if (req.query.skills) params.append('skills', req.query.skills);

  try {
    const resp = await apiRequest(req, res, 'get', `/api/v1/profiles/export/csv?${params.toString()}`);
    res.setHeader('Content-Type', 'text/csv');
    res.setHeader('Content-Disposition', 'attachment; filename=profiles.csv');
    res.send(resp.data);
  } catch {
    res.status(500).send('Export failed');
  }
});

app.get('/profiles/new', requireAuth, async (req, res) => {
  const user = await getCurrentUser(req, res);
  if (!user) return res.redirect('/login');
  res.render('profile-form', { title: 'New Profile', user, profile: null, csrfToken: req.csrfToken, error: null });
});

app.post('/profiles/new', requireAuth, csrfProtect, async (req, res) => {
  const user = await getCurrentUser(req, res);
  if (!user) return res.redirect('/login');

  const data = {
    full_name: req.body.full_name,
    email: req.body.email || null,
    phone: req.body.phone || null,
    location: req.body.location || null,
    skills: req.body.skills ? req.body.skills.split(',').map(s => s.trim()).filter(Boolean) : [],
    bio: req.body.bio || null,
    github_username: req.body.github_username || null,
    company: req.body.company || null,
    role_title: req.body.role_title || null,
    years_of_experience: req.body.years_of_experience ? parseInt(req.body.years_of_experience) : null,
  };

  try {
    await apiRequest(req, res, 'post', '/api/v1/profiles', data);
    res.redirect('/profiles');
  } catch (err) {
    const message = err.response?.data?.detail || 'Failed to create profile';
    res.render('profile-form', { title: 'New Profile', user, profile: data, csrfToken: req.csrfToken, error: message });
  }
});

app.get('/profiles/:id', requireAuth, async (req, res) => {
  const user = await getCurrentUser(req, res);
  if (!user) return res.redirect('/login');

  try {
    const resp = await apiRequest(req, res, 'get', `/api/v1/profiles/${req.params.id}`);
    res.render('profile-detail', { title: resp.data.full_name, user, profile: resp.data, csrfToken: req.csrfToken });
  } catch {
    res.status(404).render('error', { title: 'Not Found', message: 'Profile not found', user, csrfToken: req.csrfToken });
  }
});

app.post('/profiles/:id/delete', requireAuth, csrfProtect, async (req, res) => {
  try {
    await apiRequest(req, res, 'delete', `/api/v1/profiles/${req.params.id}`);
    res.redirect('/profiles');
  } catch (err) {
    const message = err.response?.data?.detail || 'Failed to delete profile';
    res.status(403).render('error', { title: 'Error', message, user: null, csrfToken: req.csrfToken });
  }
});

/* ── Users (admin) ── */
app.get('/users', requireAuth, async (req, res) => {
  const user = await getCurrentUser(req, res);
  if (!user) return res.redirect('/login');
  if (user.role !== 'admin') {
    return res.status(403).render('error', { title: 'Forbidden', message: 'Admin access required', user, csrfToken: req.csrfToken });
  }

  let users = [];
  try {
    const resp = await apiRequest(req, res, 'get', '/api/v1/users');
    users = resp.data;
  } catch { /* ignore */ }

  res.render('users', { title: 'Users', user, users, csrfToken: req.csrfToken });
});

app.post('/users/:id/role', requireAuth, csrfProtect, async (req, res) => {
  try {
    await apiRequest(req, res, 'patch', `/api/v1/users/${req.params.id}/role`, { role: req.body.role });
  } catch { /* ignore */ }
  res.redirect('/users');
});

/* ── Root ── */
app.get('/', (req, res) => {
  if (req.cookies.access_token) return res.redirect('/dashboard');
  res.redirect('/login');
});

app.listen(PORT, () => {
  console.log(`Web portal running on port ${PORT}`);
});
