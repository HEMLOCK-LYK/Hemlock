/**
 * Hemlock 主题上下文
 *
 * 提供多主题切换能力，支持 6 个预设主题及自定义主题变量覆盖。
 * 主题选择持久化到 localStorage，切换时即时生效（修改 CSS 变量，无需刷新）。
 */

import {
  createContext,
  useContext,
  useState,
  useLayoutEffect,
  useCallback,
} from 'react';
import type { ReactNode } from 'react';
import {
  THEME_VARS,
  THEMES,
  THEME_STORAGE_KEY,
} from '../themes';
import type { ThemeName, ThemeOption } from '../themes';

/**
 * 主题上下文值
 */
interface ThemeContextValue {
  /** 当前主题名称 */
  currentTheme: ThemeName;
  /** 所有可用主题列表 */
  themes: ThemeOption[];
  /** 切换到指定预设主题 */
  setTheme: (theme: ThemeName) => void;
  /** 应用自定义主题变量（覆盖当前主题的指定 CSS 变量） */
  setCustomTheme: (vars: Record<string, string>) => void;
  /** 重置为当前预设主题（清除所有自定义覆盖） */
  resetCustomTheme: () => void;
}

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);

/**
 * 将指定主题的 CSS 变量应用到 document.documentElement
 *
 * @param themeName 主题名称
 */
function applyTheme(themeName: ThemeName): void {
  const vars = THEME_VARS[themeName];
  if (!vars) return;

  const root = document.documentElement;
  root.setAttribute('data-theme', themeName);
  Object.entries(vars).forEach(([key, value]) => {
    root.style.setProperty(key, value);
  });
}

/**
 * 应用自定义主题变量（在当前主题基础上覆盖指定变量）
 *
 * @param vars 自定义 CSS 变量键值对
 */
function applyCustomVars(vars: Record<string, string>): void {
  const root = document.documentElement;
  Object.entries(vars).forEach(([key, value]) => {
    root.style.setProperty(key, value);
  });
}

/**
 * 清除所有内联 CSS 变量，然后重新应用当前预设主题
 *
 * @param themeName 当前预设主题名称
 */
function resetToPresetTheme(themeName: ThemeName): void {
  const root = document.documentElement;
  // 先清除所有主题相关的内联变量
  const allThemeKeys = Object.keys(THEME_VARS[themeName] ?? {});
  allThemeKeys.forEach((key) => {
    root.style.removeProperty(key);
  });
  // 重新应用预设主题
  applyTheme(themeName);
}

/**
 * 主题 Provider 组件
 *
 * 在应用根节点包裹此组件，即可在任意子组件中通过 useTheme() 获取主题状态。
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  // 从 localStorage 恢复主题，默认为 light
  // 在初始化阶段同步应用主题，避免首次渲染时的样式闪烁（FOUC）
  const [currentTheme, setCurrentTheme] = useState<ThemeName>(() => {
    let theme: ThemeName = 'light';
    try {
      const saved = localStorage.getItem(THEME_STORAGE_KEY) as ThemeName | null;
      if (saved && THEME_VARS[saved]) {
        theme = saved;
      }
    } catch {
      // localStorage 不可用时使用默认主题
    }
    // 初始化时同步应用主题 CSS 变量，确保首次渲染即有正确样式
    applyTheme(theme);
    return theme;
  });

  // 主题变化时应用 CSS 变量（使用 useLayoutEffect 在浏览器绘制前同步执行）
  useLayoutEffect(() => {
    applyTheme(currentTheme);
  }, [currentTheme]);

  /**
   * 切换到指定预设主题
   */
  const setTheme = useCallback((theme: ThemeName) => {
    setCurrentTheme(theme);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // localStorage 不可用时忽略
    }
  }, []);

  /**
   * 应用自定义主题变量（覆盖当前主题的特定 CSS 变量）
   */
  const setCustomTheme = useCallback((vars: Record<string, string>) => {
    applyCustomVars(vars);
  }, []);

  /**
   * 重置为当前预设主题（清除所有自定义覆盖）
   */
  const resetCustomTheme = useCallback(() => {
    resetToPresetTheme(currentTheme);
  }, [currentTheme]);

  const value: ThemeContextValue = {
    currentTheme,
    themes: THEMES,
    setTheme,
    setCustomTheme,
    resetCustomTheme,
  };

  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}

/**
 * 主题 Hook，必须在 ThemeProvider 内部使用
 *
 * @returns 主题上下文值
 * @throws 如果在 ThemeProvider 外部调用
 */
export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error('useTheme 必须在 ThemeProvider 内部使用');
  }
  return ctx;
}
