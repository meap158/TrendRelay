import { NextRequest, NextResponse } from 'next/server';

const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '[::1]']);

export async function GET(request: NextRequest) {
  if (
    process.env.TRENDRELAY_LOCAL_ADMIN !== 'true' ||
    !LOOPBACK_HOSTS.has(request.nextUrl.hostname)
  ) {
    return NextResponse.json({ error: 'Not available.' }, { status: 404 });
  }

  const email = process.env.POSTIZ_LOCAL_ADMIN_EMAIL;
  const password = process.env.POSTIZ_LOCAL_ADMIN_PASSWORD;
  const backend = process.env.BACKEND_INTERNAL_URL;
  if (!email || !password || !backend) {
    return NextResponse.json(
      { error: 'Local Postiz admin is not configured.' },
      { status: 503 },
    );
  }

  const login = await fetch(`${backend}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, provider: 'LOCAL' }),
    cache: 'no-store',
  });
  const auth = login.headers.get('auth');
  if (!login.ok || !auth) {
    return NextResponse.json(
      { error: 'Could not establish the local Postiz session.' },
      { status: 502 },
    );
  }

  const response = NextResponse.redirect(new URL('/launches', request.url));
  response.cookies.set('auth', auth, {
    httpOnly: false,
    sameSite: 'lax',
    maxAge: 60 * 60 * 24 * 365,
    path: '/',
  });
  const organization = login.headers.get('showorg');
  if (organization) {
    response.cookies.set('showorg', organization, {
      httpOnly: false,
      sameSite: 'lax',
      maxAge: 60 * 60 * 24 * 365,
      path: '/',
    });
  }
  return response;
}
