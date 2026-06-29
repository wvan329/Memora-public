// ========================= 认证 Store =========================
import { ref, computed, readonly } from 'vue';
import { defineStore } from 'pinia';
import { apiPath } from '@/utils/api';
import { STORAGE_KEYS } from '@/utils/storageKeys';

/**
 * 从 Android NativeBridge 同步获取密码。
 * 仅 JS ↔ Kotlin 桥接传输，不经过网络。
 * 前端初始化时若 localStorage 为空则调用，避免 LoginOverlay 二次弹窗。
 */
function getNativePassword(): string {
  try {
    const bridge = (window as any).NativeBridge;
    return bridge?.getPassword?.() || '';
  } catch { return ''; }
}

export const useAuthStore = defineStore('auth', () => {
  // 优先从 NativeBridge 获取密码（Android 端），其次从 localStorage 获取
  if (!localStorage.getItem(STORAGE_KEYS.AUTH_PASSWORD)) {
    const pwd = getNativePassword();
    if (pwd) {
      localStorage.setItem(STORAGE_KEYS.AUTH_PASSWORD, pwd);
    }
  }
  const password = ref(localStorage.getItem(STORAGE_KEYS.AUTH_PASSWORD) || '');
  const needsLogin = computed(() => !password.value);

  async function login(pwd: string): Promise<{ ok: boolean; error?: string }> {
    try {
      const resp = await fetch(apiPath('/api/login'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: pwd }),
      });
      if (!resp.ok) {
        const data = await resp.json();
        return { ok: false, error: data.error || '密码错误' };
      }
      password.value = pwd;
      localStorage.setItem(STORAGE_KEYS.AUTH_PASSWORD, pwd);
      return { ok: true };
    } catch {
      return { ok: false, error: '网络错误，请重试' };
    }
  }

  function logout() {
    password.value = '';
    localStorage.removeItem(STORAGE_KEYS.AUTH_PASSWORD);
  }

  function handle401() {
    logout();
  }

  return { password: readonly(password), needsLogin, login, logout, handle401 };
});