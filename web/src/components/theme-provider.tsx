import { ThemeEnum } from '@/constants/common';
import React, { createContext, useContext, useEffect } from 'react';

type ThemeProviderProps = {
  children: React.ReactNode;
  defaultTheme?: ThemeEnum;
  storageKey?: string;
};

type ThemeProviderState = {
  theme: ThemeEnum;
  setTheme: (theme: ThemeEnum) => void;
};

const initialState: ThemeProviderState = {
  theme: ThemeEnum.Light,
  setTheme: () => null,
};

const ThemeProviderContext = createContext<ThemeProviderState>(initialState);

/**
 * 全局固定亮色主题：不再支持 Dark/System。
 * 忽略 localStorage 存储与 defaultTheme 参数，setTheme 保留为空实现以兼容旧调用方。
 */
export function ThemeProvider({ children, ...props }: ThemeProviderProps) {
  const theme = ThemeEnum.Light;

  useEffect(() => {
    const root = window.document.documentElement;
    root.classList.remove(ThemeEnum.Light, ThemeEnum.Dark);
    localStorage.setItem('ragflow-ui-theme', ThemeEnum.Light);
    root.classList.add(theme);
  }, [theme]);

  return (
    <ThemeProviderContext.Provider
      {...props}
      value={{
        theme,
        setTheme: () => null,
      }}
    >
      {children}
    </ThemeProviderContext.Provider>
  );
}

export const useTheme = () => {
  const context = useContext(ThemeProviderContext);

  if (context === undefined)
    throw new Error('useTheme must be used within a ThemeProvider');

  return context;
};

export const useIsDarkTheme = () => {
  const { theme } = useTheme();

  return theme === ThemeEnum.Dark;
};

export function useSyncThemeFromParams(_theme: string | null) {
  // 主题已固定为亮色，保留空实现以兼容旧调用方
  void _theme;
}
