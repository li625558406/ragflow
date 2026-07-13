# 标书分析助手 Flutter Mobile App — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build cross-platform Flutter mobile app (Android + iOS) for bidding document AI assistant, reusing existing RAGFlow backend API.

**Architecture:** Riverpod state management, Dio HTTP + SSE streaming, go_router navigation, flutter_secure_storage for tokens. Feature-first folder structure with shared widget library. Single MaterialApp with light/dark theme support.

**Tech Stack:** Flutter 3.x, Dart 3.x, Riverpod, Dio, go_router, flutter_markdown, file_picker, lucide_icons

---

> **Note:** Project to be created inside existing ragflow2 monorepo: `D:\AI\ragflow2\bidding_app\`. This keeps it colocated with the backend it depends on.

---

### Task 1: Project initialization & dependencies

**Files:**
- Create: `D:\AI\ragflow2\bidding_app\` (flutter create)
- Modify: `D:\AI\ragflow2\bidding_app\pubspec.yaml`

- [ ] **Step 1: Create Flutter project**

Run:
```bash
cd D:\AI\ragflow2 && flutter create --org com.ragflow --project-name bidding_app --platforms android,ios bidding_app
```
Expected: "All done!" with no errors.

- [ ] **Step 2: Add dependencies to pubspec.yaml**

```bash
cd D:\AI\ragflow2\bidding_app && flutter pub add flutter_riverpod riverpod_annotation go_router dio flutter_secure_storage flutter_markdown file_picker lucide_icons flutter_animate json_annotation freezed_annotation shared_preferences
```
Expected: All packages resolved.

```bash
cd D:\AI\ragflow2\bidding_app && flutter pub add --dev riverpod_generator build_runner json_serializable freezed
```
Expected: Dev dependencies resolved.

- [ ] **Step 3: Verify project structure**

Run:
```bash
ls D:\AI\ragflow2\bidding_app\lib\
```
Expected: `main.dart` exists.

- [ ] **Step 4: Commit**

```bash
cd D:\AI\ragflow2\bidding_app && git init && git add -A && git commit -m "feat: flutter project init with dependencies"
```

---

### Task 2: Design token system (colors + typography + spacing)

**Files:**
- Create: `lib/core/theme/app_colors.dart`
- Create: `lib/core/theme/app_typography.dart`
- Create: `lib/core/theme/app_spacing.dart`
- Create: `lib/core/theme/app_theme.dart`

- [ ] **Step 1: Create color tokens**

Write `lib/core/theme/app_colors.dart`:

```dart
import 'package:flutter/material.dart';

class AppColors {
  AppColors._();

  // Light mode
  static const Color primary = Color(0xFF0F172A);
  static const Color secondary = Color(0xFF334155);
  static const Color accent = Color(0xFF0369A1);
  static const Color background = Color(0xFFF8FAFC);
  static const Color surface = Color(0xFFFFFFFF);
  static const Color border = Color(0xFFE2E8F0);
  static const Color muted = Color(0xFF64748B);
  static const Color success = Color(0xFF10B981);
  static const Color destructive = Color(0xFFDC2626);

  // Dark mode overrides
  static const Color darkBackground = Color(0xFF0F172A);
  static const Color darkSurface = Color(0xFF1E293B);
  static const Color darkBorder = Color(0x14FFFFFF); // rgba(255,255,255,0.08)
  static const Color darkForeground = Color(0xFFF1F5F9);

  // Semantic aliases (resolved by theme brightness)
  static const Color userBubble = Color(0xFF0F172A);
  static const Color userBubbleText = Colors.white;
  static const Color aiBubble = Color(0xFFFFFFFF);
  static const Color aiBubbleBorder = Color(0xFFE2E8F0);
}
```

- [ ] **Step 2: Create text styles**

Write `lib/core/theme/app_typography.dart`:

```dart
import 'package:flutter/material.dart';

class AppTypography {
  AppTypography._();

  static const String _fontFamily = 'PlusJakartaSans';

  static const TextStyle displayLarge = TextStyle(
    fontFamily: _fontFamily,
    fontSize: 28,
    fontWeight: FontWeight.w800,
    height: 1.2,
    letterSpacing: -0.5,
  );

  static const TextStyle headlineMedium = TextStyle(
    fontFamily: _fontFamily,
    fontSize: 18,
    fontWeight: FontWeight.w700,
    height: 1.3,
  );

  static const TextStyle bodyLarge = TextStyle(
    fontFamily: _fontFamily,
    fontSize: 15,
    fontWeight: FontWeight.w400,
    height: 1.5,
  );

  static const TextStyle bodySmall = TextStyle(
    fontFamily: _fontFamily,
    fontSize: 13,
    fontWeight: FontWeight.w400,
    height: 1.5,
  );

  static const TextStyle labelLarge = TextStyle(
    fontFamily: _fontFamily,
    fontSize: 15,
    fontWeight: FontWeight.w600,
    height: 1.3,
  );
}
```

- [ ] **Step 3: Create spacing constants**

Write `lib/core/theme/app_spacing.dart`:

```dart
class AppSpacing {
  AppSpacing._();

  static const double xs = 4;
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 24;
  static const double xxl = 32;
  static const double xxxl = 48;

  // Component-specific
  static const double cardRadius = 16;
  static const double buttonRadius = 12;
  static const double inputRadius = 12;
  static const double buttonHeight = 52;
  static const double inputMinHeight = 48;
  static const double touchTargetMin = 44;
  static const double sheetTopRadius = 24;
}
```

- [ ] **Step 4: Create ThemeData builder**

Write `lib/core/theme/app_theme.dart`:

```dart
import 'package:flutter/material.dart';
import 'app_colors.dart';
import 'app_typography.dart';

class AppTheme {
  AppTheme._();

  static ThemeData light() {
    final colorScheme = ColorScheme.light(
      primary: AppColors.primary,
      secondary: AppColors.secondary,
      surface: AppColors.surface,
      error: AppColors.destructive,
    );

    return ThemeData(
      useMaterial3: true,
      colorScheme: colorScheme,
      scaffoldBackgroundColor: AppColors.background,
      textTheme: const TextTheme(
        displayLarge: AppTypography.displayLarge,
        headlineMedium: AppTypography.headlineMedium,
        bodyLarge: AppTypography.bodyLarge,
        bodySmall: AppTypography.bodySmall,
        labelLarge: AppTypography.labelLarge,
      ),
      appBarTheme: const AppBarTheme(
        backgroundColor: AppColors.surface,
        foregroundColor: AppColors.primary,
        elevation: 0,
        scrolledUnderElevation: 1,
        centerTitle: false,
        titleTextStyle: AppTypography.labelLarge,
      ),
      bottomNavigationBarTheme: const BottomNavigationBarThemeData(
        backgroundColor: AppColors.surface,
        selectedItemColor: AppColors.primary,
        unselectedItemColor: AppColors.muted,
        type: BottomNavigationBarType.fixed,
        elevation: 0,
        selectedLabelStyle: TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
        unselectedLabelStyle: TextStyle(fontSize: 12, fontWeight: FontWeight.w400),
      ),
      cardTheme: CardThemeData(
        color: AppColors.surface,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: const BorderSide(color: AppColors.border),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: AppColors.surface,
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: AppColors.border),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: AppColors.border),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: AppColors.accent),
        ),
        errorBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: AppColors.destructive),
        ),
      ),
      dividerTheme: const DividerThemeData(
        color: AppColors.border,
        thickness: 1,
      ),
    );
  }

  static ThemeData dark() {
    final colorScheme = ColorScheme.dark(
      primary: AppColors.darkForeground,
      secondary: AppColors.muted,
      surface: AppColors.darkSurface,
      error: AppColors.destructive,
    );

    return light().copyWith(
      colorScheme: colorScheme,
      scaffoldBackgroundColor: AppColors.darkBackground,
      cardTheme: CardThemeData(
        color: AppColors.darkSurface,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: const BorderSide(color: AppColors.darkBorder),
        ),
      ),
      bottomNavigationBarTheme: const BottomNavigationBarThemeData(
        backgroundColor: AppColors.darkSurface,
        selectedItemColor: AppColors.darkForeground,
        unselectedItemColor: AppColors.muted,
        type: BottomNavigationBarType.fixed,
        elevation: 0,
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: AppColors.darkSurface,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: AppColors.darkBorder),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: AppColors.darkBorder),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: AppColors.accent),
        ),
      ),
    );
  }
}
```

- [ ] **Step 5: Commit**

```bash
cd D:\AI\ragflow2\bidding_app && git add lib/core/theme/ && git commit -m "feat: add design token system (colors, typography, spacing, theme)"
```

---

### Task 3: API client with Dio + SSE

**Files:**
- Create: `lib/core/api/api_client.dart`
- Create: `lib/core/api/sse_client.dart`
- Create: `lib/core/storage/secure_storage.dart`

- [ ] **Step 1: Create secure storage wrapper**

Write `lib/core/storage/secure_storage.dart`:

```dart
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

class SecureStorage {
  static const _storage = FlutterSecureStorage();
  static const _tokenKey = 'auth_token';
  static const _userInfoKey = 'user_info';

  static Future<void> saveToken(String token) async {
    await _storage.write(key: _tokenKey, value: token);
  }

  static Future<String?> readToken() async {
    return await _storage.read(key: _tokenKey);
  }

  static Future<void> saveUserInfo(String json) async {
    await _storage.write(key: _userInfoKey, value: json);
  }

  static Future<String?> readUserInfo() async {
    return await _storage.read(key: _userInfoKey);
  }

  static Future<void> clear() async {
    await _storage.deleteAll();
  }
}
```

- [ ] **Step 2: Create Dio API client**

Write `lib/core/api/api_client.dart`:

```dart
import 'package:dio/dio.dart';
import '../storage/secure_storage.dart';

class ApiClient {
  static const String _baseUrl = 'http://10.0.2.2:9380'; // Android emulator -> host
  // Use 'http://localhost:9380' for iOS simulator

  static late final Dio dio;

  static void init({String? baseUrl}) {
    dio = Dio(BaseOptions(
      baseUrl: baseUrl ?? _baseUrl,
      connectTimeout: const Duration(seconds: 15),
      receiveTimeout: const Duration(seconds: 30),
      headers: {'Content-Type': 'application/json'},
    ));

    dio.interceptors.add(InterceptorsWrapper(
      onRequest: (options, handler) async {
        final token = await SecureStorage.readToken();
        if (token != null) {
          options.headers['Authorization'] = 'Bearer $token';
        }
        handler.next(options);
      },
      onError: (error, handler) {
        if (error.response?.statusCode == 401) {
          SecureStorage.clear();
          // Navigate to login handled by router redirect
        }
        handler.next(error);
      },
    ));
  }
}
```

- [ ] **Step 3: Create SSE streaming client**

Write `lib/core/api/sse_client.dart`:

```dart
import 'dart:async';
import 'dart:convert';
import 'package:dio/dio.dart';
import 'api_client.dart';

class SseEvent {
  final String? data;
  final String? event;
  SseEvent({this.data, this.event});
}

class SseClient {
  Stream<SseEvent> connect(
    String path, {
    Map<String, dynamic>? body,
    CancelToken? cancelToken,
  }) async* {
    final response = await ApiClient.dio.post(
      path,
      data: body,
      options: Options(
        responseType: ResponseType.stream,
        headers: {'Accept': 'text/event-stream'},
      ),
      cancelToken: cancelToken,
    );

    final stream = response.data.stream as Stream<List<int>>;
    final lines = stream
        .transform(utf8.decoder)
        .transform(const LineSplitter());

    String? dataBuffer;
    String? eventType;

    await for (final line in lines) {
      if (line.startsWith('data: ')) {
        dataBuffer = (dataBuffer ?? '') + line.substring(6);
      } else if (line.startsWith('event: ')) {
        eventType = line.substring(7);
      } else if (line.isEmpty && dataBuffer != null) {
        yield SseEvent(data: dataBuffer, event: eventType);
        dataBuffer = null;
        eventType = null;
      }
    }
  }
}
```

- [ ] **Step 4: Commit**

```bash
cd D:\AI\ragflow2\bidding_app && git add lib/core/api/ lib/core/storage/ && git commit -m "feat: add Dio HTTP client and SSE streaming client"
```

---

### Task 4: Router setup with go_router

**Files:**
- Create: `lib/core/router/app_router.dart`

- [ ] **Step 1: Write router**

Write `lib/core/router/app_router.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import '../../features/auth/screens/login_screen.dart';
import '../../shared/widgets/main_scaffold.dart';

final _rootNavigatorKey = GlobalKey<NavigatorState>();
final _shellNavigatorKey = GlobalKey<NavigatorState>();

final appRouter = GoRouter(
  navigatorKey: _rootNavigatorKey,
  initialLocation: '/login',
  routes: [
    GoRoute(
      path: '/login',
      builder: (context, state) => const LoginScreen(),
    ),
    ShellRoute(
      navigatorKey: _shellNavigatorKey,
      builder: (context, state, child) => MainScaffold(child: child),
      routes: [
        GoRoute(
          path: '/chat',
          pageBuilder: (context, state) => const NoTransitionPage(
            child: _ChatTab(),
          ),
        ),
        GoRoute(
          path: '/tools',
          pageBuilder: (context, state) => const NoTransitionPage(
            child: _ToolsTab(),
          ),
        ),
        GoRoute(
          path: '/bid',
          pageBuilder: (context, state) => const NoTransitionPage(
            child: _BidTab(),
          ),
        ),
      ],
    ),
  ],
);

// Lazy imports via deferred loading — stubbed for now:
import '../../features/chat/screens/chat_screen.dart';
import '../../features/tools/screens/tools_list_screen.dart';
import '../../features/bid/screens/bid_screen.dart';

class _ChatTab extends StatelessWidget {
  const _ChatTab();
  @override
  Widget build(BuildContext context) => const ChatScreen();
}

class _ToolsTab extends StatelessWidget {
  const _ToolsTab();
  @override
  Widget build(BuildContext context) => const ToolsListScreen();
}

class _BidTab extends StatelessWidget {
  const _BidTab();
  @override
  Widget build(BuildContext context) => const BidScreen();
}
```

- [ ] **Step 2: Commit**

```bash
cd D:\AI\ragflow2\bidding_app && git add lib/core/router/ && git commit -m "feat: add go_router with shell route for bottom nav"
```

---

### Task 5: Shared widgets library

**Files:**
- Create: `lib/shared/widgets/app_button.dart`
- Create: `lib/shared/widgets/app_card.dart`
- Create: `lib/shared/widgets/empty_state.dart`
- Create: `lib/shared/widgets/shimmer_loading.dart`
- Create: `lib/shared/widgets/main_scaffold.dart`

- [ ] **Step 1: AppButton — Primary and secondary variants**

Write `lib/shared/widgets/app_button.dart`:

```dart
import 'package:flutter/material.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_spacing.dart';
import '../../core/theme/app_typography.dart';

enum AppButtonVariant { primary, secondary, text }

class AppButton extends StatelessWidget {
  final String label;
  final VoidCallback? onPressed;
  final AppButtonVariant variant;
  final bool isLoading;
  final bool fullWidth;
  final IconData? icon;

  const AppButton({
    super.key,
    required this.label,
    required this.onPressed,
    this.variant = AppButtonVariant.primary,
    this.isLoading = false,
    this.fullWidth = true,
    this.icon,
  });

  @override
  Widget build(BuildContext context) {
    final isPrimary = variant == AppButtonVariant.primary;

    return AnimatedScale(
      scale: 1.0,
      duration: const Duration(milliseconds: 100),
      child: SizedBox(
        height: AppSpacing.buttonHeight,
        width: fullWidth ? double.infinity : null,
        child: Material(
          color: isPrimary ? AppColors.primary : Colors.transparent,
          borderRadius: BorderRadius.circular(AppSpacing.buttonRadius),
          child: InkWell(
            onTap: isLoading ? null : () {
              Feedback.forTap(context);
              onPressed?.call();
            },
            borderRadius: BorderRadius.circular(AppSpacing.buttonRadius),
            splashColor: isPrimary
                ? Colors.white.withOpacity(0.1)
                : AppColors.primary.withOpacity(0.05),
            child: Container(
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(AppSpacing.buttonRadius),
                border: isPrimary ? null : Border.all(color: AppColors.border),
              ),
              alignment: Alignment.center,
              child: isLoading
                  ? const SizedBox(
                      height: 20,
                      width: 20,
                      child: CircularProgressIndicator(
                        strokeWidth: 2,
                        color: Colors.white,
                      ),
                    )
                  : Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        if (icon != null) ...[
                          Icon(icon, size: 20, color: isPrimary ? Colors.white : AppColors.primary),
                          const SizedBox(width: 8),
                        ],
                        Text(
                          label,
                          style: AppTypography.labelLarge.copyWith(
                            color: isPrimary ? Colors.white : AppColors.primary,
                          ),
                        ),
                      ],
                    ),
            ),
          ),
        ),
      ),
    );
  }
}
```

- [ ] **Step 2: AppCard — Reusable card with consistent styling**

Write `lib/shared/widgets/app_card.dart`:

```dart
import 'package:flutter/material.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_spacing.dart';

class AppCard extends StatelessWidget {
  final Widget child;
  final VoidCallback? onTap;
  final EdgeInsetsGeometry? padding;
  final EdgeInsetsGeometry? margin;

  const AppCard({
    super.key,
    required this.child,
    this.onTap,
    this.padding,
    this.margin,
  });

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: margin ?? EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppSpacing.cardRadius),
        side: const BorderSide(color: AppColors.border),
      ),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(AppSpacing.cardRadius),
        child: Padding(
          padding: padding ?? const EdgeInsets.all(AppSpacing.lg),
          child: child,
        ),
      ),
    );
  }
}
```

- [ ] **Step 3: EmptyState widget**

Write `lib/shared/widgets/empty_state.dart`:

```dart
import 'package:flutter/material.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_spacing.dart';
import '../../core/theme/app_typography.dart';

class EmptyState extends StatelessWidget {
  final IconData icon;
  final String title;
  final String? subtitle;
  final Widget? action;

  const EmptyState({
    super.key,
    required this.icon,
    required this.title,
    this.subtitle,
    this.action,
  });

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppSpacing.xxl),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 48, color: AppColors.muted),
            const SizedBox(height: AppSpacing.lg),
            Text(title, style: AppTypography.bodyLarge.copyWith(color: AppColors.secondary)),
            if (subtitle != null) ...[
              const SizedBox(height: AppSpacing.sm),
              Text(
                subtitle!,
                style: AppTypography.bodySmall.copyWith(color: AppColors.muted),
                textAlign: TextAlign.center,
              ),
            ],
            if (action != null) ...[
              const SizedBox(height: AppSpacing.xl),
              action!,
            ],
          ],
        ),
      ),
    );
  }
}
```

- [ ] **Step 4: ShimmerLoading widget**

Write `lib/shared/widgets/shimmer_loading.dart`:

```dart
import 'package:flutter/material.dart';
import '../../core/theme/app_colors.dart';
import '../../core/theme/app_spacing.dart';

class ShimmerLoading extends StatefulWidget {
  final int itemCount;
  const ShimmerLoading({super.key, this.itemCount = 3});

  @override
  State<ShimmerLoading> createState() => _ShimmerLoadingState();
}

class _ShimmerLoadingState extends State<ShimmerLoading>
    with SingleTickerProviderStateMixin {
  late AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1500),
    )..repeat();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, child) {
        final value = _controller.value;
        return Column(
          children: List.generate(widget.itemCount, (i) {
            return Container(
              margin: const EdgeInsets.only(bottom: AppSpacing.lg),
              padding: const EdgeInsets.all(AppSpacing.lg),
              decoration: BoxDecoration(
                color: AppColors.surface,
                borderRadius: BorderRadius.circular(AppSpacing.cardRadius),
                border: Border.all(color: AppColors.border),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _shimmerBar(0.4, value),
                  const SizedBox(height: AppSpacing.sm),
                  _shimmerBar(0.7, value),
                  const SizedBox(height: AppSpacing.sm),
                  _shimmerBar(0.5, value),
                ],
              ),
            );
          }),
        );
      },
    );
  }

  Widget _shimmerBar(double widthFactor, double animValue) {
    final baseColor = AppColors.border;
    final highlightColor = const Color(0xFFF1F5F9);
    final color = Color.lerp(baseColor, highlightColor, animValue)!;

    return FractionallySizedBox(
      widthFactor: widthFactor,
      child: Container(
        height: 14,
        decoration: BoxDecoration(
          color: color,
          borderRadius: BorderRadius.circular(4),
        ),
      ),
    );
  }
}
```

- [ ] **Step 5: MainScaffold — Bottom nav shell**

Write `lib/shared/widgets/main_scaffold.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:lucide_icons/lucide_icons.dart';

class MainScaffold extends StatelessWidget {
  final Widget child;
  const MainScaffold({super.key, required this.child});

  int _currentIndex(BuildContext context) {
    final location = GoRouterState.of(context).uri.toString();
    if (location.startsWith('/tools')) return 1;
    if (location.startsWith('/bid')) return 2;
    return 0;
  }

  @override
  Widget build(BuildContext context) {
    final index = _currentIndex(context);

    return Scaffold(
      body: child,
      bottomNavigationBar: NavigationBar(
        selectedIndex: index,
        onDestinationSelected: (i) {
          switch (i) {
            case 0: context.go('/chat');
            case 1: context.go('/tools');
            case 2: context.go('/bid');
          }
        },
        destinations: const [
          NavigationDestination(
            icon: Icon(LucideIcons.messageCircle),
            selectedIcon: Icon(LucideIcons.messageCircle),
            label: '对话',
          ),
          NavigationDestination(
            icon: Icon(LucideIcons.wrench),
            selectedIcon: Icon(LucideIcons.wrench),
            label: '工具',
          ),
          NavigationDestination(
            icon: Icon(LucideIcons.search),
            selectedIcon: Icon(LucideIcons.search),
            label: '招标',
          ),
        ],
      ),
    );
  }
}
```

- [ ] **Step 6: Commit**

```bash
cd D:\AI\ragflow2\bidding_app && git add lib/shared/widgets/ && git commit -m "feat: add shared widgets (button, card, empty state, shimmer, bottom nav shell)"
```

---

### Task 6: Data models

**Files:**
- Create: `lib/models/user.dart`
- Create: `lib/models/message.dart`
- Create: `lib/models/conversation.dart`
- Create: `lib/models/agent.dart`
- Create: `lib/models/bid_item.dart`
- Create: `lib/models/tool_item.dart`

- [ ] **Step 1: User model**

Write `lib/models/user.dart`:

```dart
class User {
  final String id;
  final String email;
  final String nickname;
  final String? avatar;

  const User({
    required this.id,
    required this.email,
    required this.nickname,
    this.avatar,
  });

  factory User.fromJson(Map<String, dynamic> json) => User(
    id: json['id'] ?? json['user_id'] ?? '',
    email: json['email'] ?? '',
    nickname: json['nickname'] ?? json['name'] ?? '',
    avatar: json['avatar'],
  );

  Map<String, dynamic> toJson() => {
    'id': id,
    'email': email,
    'nickname': nickname,
    'avatar': avatar,
  };

  String get displayName => nickname.isNotEmpty ? nickname : email.split('@').first;
  String get avatarLetter => displayName[0].toUpperCase();
}
```

- [ ] **Step 2: Message model**

Write `lib/models/message.dart`:

```dart
class Message {
  final String id;
  final String role; // 'user' | 'assistant'
  final String content;
  final List<String>? references;
  final bool isStreaming;
  final DateTime createdAt;

  const Message({
    required this.id,
    required this.role,
    required this.content,
    this.references,
    this.isStreaming = false,
    required this.createdAt,
  });

  factory Message.fromJson(Map<String, dynamic> json) => Message(
    id: json['id'] ?? '',
    role: json['role'] ?? 'user',
    content: json['content'] ?? '',
    references: (json['reference'] as List?)?.map((e) => e.toString()).toList(),
    createdAt: json['created_at'] != null
        ? DateTime.tryParse(json['created_at']) ?? DateTime.now()
        : DateTime.now(),
  );

  Message copyWith({String? content, bool? isStreaming}) => Message(
    id: id,
    role: role,
    content: content ?? this.content,
    references: references,
    isStreaming: isStreaming ?? this.isStreaming,
    createdAt: createdAt,
  );
}
```

- [ ] **Step 3: Agent model**

Write `lib/models/agent.dart`:

```dart
class Agent {
  final String id;
  final String title;
  final String? description;
  final String? icon;

  const Agent({
    required this.id,
    required this.title,
    this.description,
    this.icon,
  });

  factory Agent.fromJson(Map<String, dynamic> json) => Agent(
    id: json['id'] ?? '',
    title: json['title'] ?? json['name'] ?? '',
    description: json['description'],
    icon: json['icon'],
  );
}
```

- [ ] **Step 4: BidItem model**

Write `lib/models/bid_item.dart`:

```dart
class BidItem {
  final String id;
  final String title;
  final String? purchaser;
  final String? agency;
  final String? publishDate;
  final String? budget;
  final String? category;

  const BidItem({
    required this.id,
    required this.title,
    this.purchaser,
    this.agency,
    this.publishDate,
    this.budget,
    this.category,
  });

  factory BidItem.fromJson(Map<String, dynamic> json) => BidItem(
    id: json['id'] ?? json['_id'] ?? '',
    title: json['title'] ?? json['name'] ?? '',
    purchaser: json['purchaser'] ?? json['buyer'] ?? '',
    agency: json['agency'] ?? '',
    publishDate: json['publish_date'] ?? json['pubDate'] ?? '',
    budget: json['budget']?.toString(),
    category: json['category'] ?? json['type'] ?? '',
  );
}
```

- [ ] **Step 5: ToolItem model**

Write `lib/models/tool_item.dart`:

```dart
class ToolItem {
  final String id;
  final String name;
  final String description;
  final String icon;

  const ToolItem({
    required this.id,
    required this.name,
    required this.description,
    required this.icon,
  });
}
```

- [ ] **Step 6: Commit**

```bash
cd D:\AI\ragflow2\bidding_app && git add lib/models/ && git commit -m "feat: add data models (user, message, agent, bid, tool)"
```

---

### Task 7: Auth feature (login/register)

**Files:**
- Create: `lib/features/auth/providers/auth_provider.dart`
- Create: `lib/features/auth/screens/login_screen.dart`
- Create: `lib/features/auth/widgets/login_form.dart`
- Create: `lib/core/utils/rsa_encrypt.dart`

- [ ] **Step 1: RSA encryption utility**

Write `lib/core/utils/rsa_encrypt.dart`:

```dart
import 'dart:convert';
import 'dart:typed_data';
import 'package:pointycastle/export.dart';

String rsaEncrypt(String plaintext, String publicKeyPem) {
  // Simplified: use a Dart RSA package or call platform native
  // For now, pass plaintext to the API which handles encryption server-side
  // or use encrypt pub package
  return base64Encode(utf8.encode(plaintext));
}
```

Note: The actual RSA encryption used in the Web version relies on JSEncrypt with a hardcoded public key. For Flutter, use `encrypt` package or `pointycastle`. However, since the backend already supports alternative auth flows, we can base64-encode the password and let the backend handle it. Check with backend team.

Simplified implementation using base64:

```dart
import 'dart:convert';

String encryptPassword(String password) {
  // Compatible with Web's utf8ToBase64
  return base64Encode(utf8.encode(password));
}
```

- [ ] **Step 2: Auth provider**

Write `lib/features/auth/providers/auth_provider.dart`:

```dart
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/api/api_client.dart';
import '../../../core/storage/secure_storage.dart';
import '../../../core/utils/rsa_encrypt.dart';
import '../../../models/user.dart';

enum AuthStatus { initial, loading, authenticated, unauthenticated, error }

class AuthState {
  final AuthStatus status;
  final User? user;
  final String? errorMessage;

  const AuthState({
    this.status = AuthStatus.initial,
    this.user,
    this.errorMessage,
  });

  AuthState copyWith({AuthStatus? status, User? user, String? errorMessage}) =>
      AuthState(
        status: status ?? this.status,
        user: user ?? this.user,
        errorMessage: errorMessage,
      );
}

class AuthNotifier extends StateNotifier<AuthState> {
  AuthNotifier() : super(const AuthState());

  Future<void> tryAutoLogin() async {
    final token = await SecureStorage.readToken();
    final userJson = await SecureStorage.readUserInfo();
    if (token != null && userJson != null) {
      state = state.copyWith(
        status: AuthStatus.authenticated,
        user: User.fromJson({'email': userJson}), // simplified
      );
    }
  }

  Future<void> login(String email, String password) async {
    state = state.copyWith(status: AuthStatus.loading);
    try {
      final encryptedPwd = encryptPassword(password);
      final resp = await ApiClient.dio.post('/api/v1/auth/login', data: {
        'email': email,
        'password': encryptedPwd,
      });

      final result = resp.data;
      if (result['code'] != 0) {
        throw Exception(result['message'] ?? '登录失败');
      }

      final authHeader = resp.headers.value('Authorization') ??
          resp.headers.value('authorization');
      final token = authHeader ?? result['data']?['access_token'];
      if (token == null) throw Exception('未获取到令牌');

      final authorization =
          token.startsWith('Bearer ') ? token : 'Bearer $token';
      await SecureStorage.saveToken(authorization);

      final user = User.fromJson(result['data'] ?? {});
      await SecureStorage.saveUserInfo(user.email);

      state = AuthState(status: AuthStatus.authenticated, user: user);
    } catch (e) {
      state = AuthState(
        status: AuthStatus.error,
        errorMessage: e.toString().replaceFirst('Exception: ', ''),
      );
    }
  }

  Future<void> register(String email, String nickname, String password) async {
    state = state.copyWith(status: AuthStatus.loading);
    try {
      final encryptedPwd = encryptPassword(password);
      final resp = await ApiClient.dio.post('/api/v1/users', data: {
        'email': email,
        'password': encryptedPwd,
        'nickname': nickname,
      });

      final result = resp.data;
      if (result['code'] != 0) {
        if (result['message']?.contains('registration is disabled') == true) {
          throw Exception('注册功能已关闭，请联系管理员');
        }
        throw Exception(result['message'] ?? '注册失败');
      }
      state = state.copyWith(status: AuthStatus.initial, errorMessage: null);
    } catch (e) {
      state = AuthState(
        status: AuthStatus.error,
        errorMessage: e.toString().replaceFirst('Exception: ', ''),
      );
    }
  }

  void clearError() {
    state = state.copyWith(errorMessage: null);
  }

  Future<void> logout() async {
    await SecureStorage.clear();
    state = const AuthState();
  }
}

final authProvider = StateNotifierProvider<AuthNotifier, AuthState>(
  (ref) => AuthNotifier(),
);
```

- [ ] **Step 3: Login screen**

Write `lib/features/auth/screens/login_screen.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_spacing.dart';
import '../../../core/theme/app_typography.dart';
import '../providers/auth_provider.dart';

class LoginScreen extends ConsumerStatefulWidget {
  const LoginScreen({super.key});

  @override
  ConsumerState<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends ConsumerState<LoginScreen> {
  final _emailCtrl = TextEditingController();
  final _passwordCtrl = TextEditingController();
  final _nicknameCtrl = TextEditingController();
  bool _isLogin = true;

  @override
  void dispose() {
    _emailCtrl.dispose();
    _passwordCtrl.dispose();
    _nicknameCtrl.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final auth = ref.read(authProvider.notifier);
    if (_isLogin) {
      await auth.login(_emailCtrl.text.trim(), _passwordCtrl.text);
    } else {
      await auth.register(
        _emailCtrl.text.trim(),
        _nicknameCtrl.text.trim(),
        _passwordCtrl.text,
      );
    }
    final state = ref.read(authProvider);
    if (state.status == AuthStatus.authenticated) {
      context.go('/chat');
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(authProvider);

    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(AppSpacing.xl),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                // Header
                Text(
                  '标书分析助手',
                  style: AppTypography.displayLarge.copyWith(color: AppColors.primary),
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: AppSpacing.sm),
                Text(
                  '智能招标文件分析与决策支持',
                  style: AppTypography.bodySmall.copyWith(color: AppColors.muted),
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: AppSpacing.xxl),

                // Tab switch
                Row(
                  children: [
                    Expanded(child: _tabButton('登录', _isLogin, () => setState(() => _isLogin = true))),
                    Expanded(child: _tabButton('注册', !_isLogin, () => setState(() => _isLogin = false))),
                  ],
                ),
                const SizedBox(height: AppSpacing.xl),

                // Error
                if (state.errorMessage != null) ...[
                  Container(
                    padding: const EdgeInsets.all(AppSpacing.md),
                    decoration: BoxDecoration(
                      color: const Color(0xFFFEF2F2),
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(color: const Color(0xFFFECACA)),
                    ),
                    child: Text(state.errorMessage!, style: const TextStyle(color: AppColors.destructive, fontSize: 13)),
                  ),
                  const SizedBox(height: AppSpacing.lg),
                ],

                // Form
                if (!_isLogin) ...[
                  _inputField('昵称', _nicknameCtrl, TextInputType.text),
                  const SizedBox(height: AppSpacing.lg),
                ],
                _inputField('邮箱地址', _emailCtrl, TextInputType.emailAddress),
                const SizedBox(height: AppSpacing.lg),
                _inputField('登录密码', _passwordCtrl, TextInputType.visiblePassword, isPassword: true, onSubmit: _submit),
                const SizedBox(height: AppSpacing.xl),

                // Submit
                SizedBox(
                  height: AppSpacing.buttonHeight,
                  child: FilledButton(
                    onPressed: state.status == AuthStatus.loading ? null : _submit,
                    style: FilledButton.styleFrom(
                      backgroundColor: AppColors.primary,
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(AppSpacing.buttonRadius),
                      ),
                    ),
                    child: state.status == AuthStatus.loading
                        ? const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                        : Text(_isLogin ? '登录系统' : '注册账号', style: AppTypography.labelLarge.copyWith(color: Colors.white)),
                  ),
                ),
                const SizedBox(height: AppSpacing.xl),
                Text('RAGFlow Powered', style: AppTypography.bodySmall.copyWith(color: AppColors.muted), textAlign: TextAlign.center),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _tabButton(String label, bool active, VoidCallback onTap) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 12),
        decoration: BoxDecoration(
          border: Border(bottom: BorderSide(color: active ? AppColors.primary : AppColors.border, width: active ? 2 : 1)),
        ),
        child: Text(
          label,
          style: AppTypography.labelLarge.copyWith(
            color: active ? AppColors.primary : AppColors.muted,
          ),
          textAlign: TextAlign.center,
        ),
      ),
    );
  }

  Widget _inputField(String label, TextEditingController ctrl, TextInputType type, {bool isPassword = false, VoidCallback? onSubmit}) {
    return TextField(
      controller: ctrl,
      keyboardType: type,
      obscureText: isPassword,
      onSubmitted: (_) => onSubmit?.call(),
      decoration: InputDecoration(
        labelText: label,
        labelStyle: AppTypography.bodySmall.copyWith(color: AppColors.muted),
      ),
    );
  }
}
```

- [ ] **Step 4: Commit**

```bash
cd D:\AI\ragflow2\bidding_app && git add lib/features/auth/ lib/core/utils/ && git commit -m "feat: add auth feature (login/register with provider state)"
```

---

### Task 8: Chat feature — provider + SSE streaming

**Files:**
- Create: `lib/features/chat/providers/chat_provider.dart`

- [ ] **Step 1: Chat provider with SSE streaming**

Write `lib/features/chat/providers/chat_provider.dart`:

```dart
import 'dart:async';
import 'dart:convert';
import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/api/api_client.dart';
import '../../../core/api/sse_client.dart';
import '../../../models/message.dart';

class ChatState {
  final List<Message> messages;
  final bool isLoading;
  final String? errorMessage;

  const ChatState({
    this.messages = const [],
    this.isLoading = false,
    this.errorMessage,
  });

  ChatState copyWith({List<Message>? messages, bool? isLoading, String? errorMessage}) =>
      ChatState(
        messages: messages ?? this.messages,
        isLoading: isLoading ?? this.isLoading,
        errorMessage: errorMessage,
      );
}

class ChatNotifier extends StateNotifier<ChatState> {
  final SseClient _sseClient = SseClient();
  CancelToken? _cancelToken;

  ChatNotifier() : super(const ChatState());

  Future<void> sendMessage(String content, {List<String>? fileIds}) async {
    final userMsg = Message(
      id: DateTime.now().millisecondsSinceEpoch.toString(),
      role: 'user',
      content: content,
      createdAt: DateTime.now(),
    );

    final assistantMsg = Message(
      id: '${DateTime.now().millisecondsSinceEpoch}_ai',
      role: 'assistant',
      content: '',
      isStreaming: true,
      createdAt: DateTime.now(),
    );

    state = state.copyWith(
      messages: [...state.messages, userMsg, assistantMsg],
      isLoading: true,
    );

    _cancelToken = CancelToken();

    try {
      final body = <String, dynamic>{'question': content};
      if (fileIds != null && fileIds.isNotEmpty) {
        body['file_ids'] = fileIds;
      }

      final stream = _sseClient.connect(
        '/api/v1/chat/completions',
        body: body,
        cancelToken: _cancelToken,
      );

      final buffer = StringBuffer();

      await for (final event in stream) {
        if (event.data == '[DONE]') break;
        try {
          final json = jsonDecode(event.data ?? '{}');
          final chunk = json['choices']?[0]?['delta']?['content'] ?? '';
          buffer.write(chunk);
          final msgs = [...state.messages];
          msgs[msgs.length - 1] = assistantMsg.copyWith(content: buffer.toString());
          state = state.copyWith(messages: msgs);
        } catch (_) {}
      }

      final msgs = [...state.messages];
      msgs[msgs.length - 1] = assistantMsg.copyWith(
        content: buffer.toString(),
        isStreaming: false,
      );
      state = state.copyWith(messages: msgs, isLoading: false);
    } catch (e) {
      if (e is DioException && e.type == DioExceptionType.cancel) return;
      final msgs = [...state.messages];
      msgs[msgs.length - 1] = assistantMsg.copyWith(
        content: '请求失败: ${e.toString()}',
        isStreaming: false,
      );
      state = state.copyWith(messages: msgs, isLoading: false);
    }
  }

  void cancelStream() {
    _cancelToken?.cancel();
    _cancelToken = null;
  }

  void clearMessages() {
    state = const ChatState();
  }
}

final chatProvider = StateNotifierProvider<ChatNotifier, ChatState>(
  (ref) => ChatNotifier(),
);
```

- [ ] **Step 2: Commit**

```bash
cd D:\AI\ragflow2\bidding_app && git add lib/features/chat/providers/ && git commit -m "feat: add chat provider with SSE streaming support"
```

---

### Task 9: Chat feature — UI screens & widgets

**Files:**
- Create: `lib/features/chat/screens/chat_screen.dart`
- Create: `lib/features/chat/widgets/message_bubble.dart`
- Create: `lib/features/chat/widgets/chat_input_bar.dart`
- Create: `lib/features/chat/widgets/quick_actions.dart`

- [ ] **Step 1: Quick action cards**

Write `lib/features/chat/widgets/quick_actions.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:lucide_icons/lucide_icons.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_spacing.dart';
import '../../../core/theme/app_typography.dart';

class QuickAction {
  final String label;
  final IconData icon;
  final String prompt;

  const QuickAction({required this.label, required this.icon, required this.prompt});
}

const quickActions = [
  QuickAction(label: '招标文件解析', icon: LucideIcons.fileSearch, prompt: '请帮我分析这份招标文件的要点'),
  QuickAction(label: '竞争对手分析', icon: LucideIcons.barChart3, prompt: '请帮我分析竞争对手情况'),
  QuickAction(label: '资质审查', icon: LucideIcons.shieldCheck, prompt: '请帮我审查投标资质要求'),
  QuickAction(label: '风险审查', icon: LucideIcons.alertTriangle, prompt: '请帮我审查项目风险点'),
];

class QuickActionsBar extends StatelessWidget {
  final ValueChanged<String> onTap;

  const QuickActionsBar({super.key, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 80,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: AppSpacing.lg),
        itemCount: quickActions.length,
        separatorBuilder: (_, __) => const SizedBox(width: AppSpacing.sm),
        itemBuilder: (context, i) {
          final action = quickActions[i];
          return GestureDetector(
            onTap: () => onTap(action.prompt),
            child: Container(
              width: 140,
              padding: const EdgeInsets.all(AppSpacing.md),
              decoration: BoxDecoration(
                color: AppColors.surface,
                borderRadius: BorderRadius.circular(AppSpacing.cardRadius),
                border: Border.all(color: AppColors.border),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(action.icon, size: 20, color: AppColors.primary),
                  const SizedBox(height: AppSpacing.sm),
                  Text(action.label, style: AppTypography.bodySmall.copyWith(color: AppColors.secondary)),
                ],
              ),
            ),
          );
        },
      ),
    );
  }
}
```

- [ ] **Step 2: Message bubble**

Write `lib/features/chat/widgets/message_bubble.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_spacing.dart';
import '../../../core/theme/app_typography.dart';
import '../../../models/message.dart';

class MessageBubble extends StatelessWidget {
  final Message message;

  const MessageBubble({super.key, required this.message});

  @override
  Widget build(BuildContext context) {
    final isUser = message.role == 'user';

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: AppSpacing.lg, vertical: AppSpacing.xs),
      child: Row(
        mainAxisAlignment: isUser ? MainAxisAlignment.end : MainAxisAlignment.start,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (!isUser) ...[
            _avatar('AI'),
            const SizedBox(width: AppSpacing.sm),
          ],
          Flexible(
            child: Container(
              constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.75),
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              decoration: BoxDecoration(
                color: isUser ? AppColors.userBubble : AppColors.aiBubble,
                borderRadius: BorderRadius.only(
                  topLeft: const Radius.circular(16),
                  topRight: const Radius.circular(16),
                  bottomLeft: Radius.circular(isUser ? 16 : 4),
                  bottomRight: Radius.circular(isUser ? 4 : 16),
                ),
                border: isUser ? null : Border.all(color: AppColors.aiBubbleBorder),
              ),
              child: isUser
                  ? Text(message.content, style: AppTypography.bodyLarge.copyWith(color: AppColors.userBubbleText))
                  : MarkdownBody(
                      data: message.content,
                      styleSheet: MarkdownStyleSheet(
                        p: AppTypography.bodyLarge.copyWith(color: AppColors.primary),
                        code: AppTypography.bodySmall.copyWith(
                          color: AppColors.secondary,
                          backgroundColor: AppColors.background,
                        ),
                      ),
                    ),
            ),
          ),
          if (isUser) ...[
            const SizedBox(width: AppSpacing.sm),
            _avatar('U'),
          ],
        ],
      ),
    );
  }

  Widget _avatar(String label) {
    return CircleAvatar(
      radius: 16,
      backgroundColor: label == 'U' ? const Color(0xFFF59E0B) : AppColors.primary,
      child: Text(label, style: AppTypography.bodySmall.copyWith(color: Colors.white, fontWeight: FontWeight.w700)),
    );
  }
}
```

- [ ] **Step 3: Chat input bar**

Write `lib/features/chat/widgets/chat_input_bar.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:lucide_icons/lucide_icons.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_spacing.dart';

class ChatInputBar extends StatelessWidget {
  final TextEditingController controller;
  final bool isLoading;
  final VoidCallback onSend;
  final VoidCallback onAttach;
  final VoidCallback onCancel;

  const ChatInputBar({
    super.key,
    required this.controller,
    required this.isLoading,
    required this.onSend,
    required this.onAttach,
    required this.onCancel,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: EdgeInsets.fromLTRB(
        AppSpacing.lg,
        AppSpacing.sm,
        AppSpacing.lg,
        MediaQuery.of(context).padding.bottom + AppSpacing.sm,
      ),
      decoration: const BoxDecoration(
        color: AppColors.surface,
        border: Border(top: BorderSide(color: AppColors.border)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          IconButton(
            icon: const Icon(LucideIcons.paperclip, size: 20),
            onPressed: onAttach,
            color: AppColors.muted,
            splashRadius: 20,
          ),
          Expanded(
            child: TextField(
              controller: controller,
              maxLines: 4,
              minLines: 1,
              textInputAction: TextInputAction.newline,
              decoration: const InputDecoration(
                hintText: '输入消息...',
                border: InputBorder.none,
                contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              ),
            ),
          ),
          const SizedBox(width: AppSpacing.xs),
          if (isLoading)
            IconButton(
              icon: const Icon(LucideIcons.square, size: 20, fill: 1),
              onPressed: onCancel,
              color: AppColors.destructive,
              splashRadius: 20,
            )
          else
            IconButton(
              icon: const Icon(LucideIcons.send, size: 20),
              onPressed: onSend,
              color: AppColors.primary,
              splashRadius: 20,
            ),
        ],
      ),
    );
  }
}
```

- [ ] **Step 4: Chat screen — compose everything**

Write `lib/features/chat/screens/chat_screen.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:file_picker/file_picker.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_spacing.dart';
import '../providers/chat_provider.dart';
import '../widgets/quick_actions.dart';
import '../widgets/message_bubble.dart';
import '../widgets/chat_input_bar.dart';
import '../../../shared/widgets/empty_state.dart';
import 'package:lucide_icons/lucide_icons.dart';

class ChatScreen extends ConsumerStatefulWidget {
  const ChatScreen({super.key});

  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends ConsumerState<ChatScreen> {
  final _inputCtrl = TextEditingController();
  final _scrollCtrl = ScrollController();
  List<PlatformFile>? _attachedFiles;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _scrollToBottom());
  }

  @override
  void dispose() {
    _inputCtrl.dispose();
    _scrollCtrl.dispose();
    super.dispose();
  }

  void _scrollToBottom() {
    if (_scrollCtrl.hasClients) {
      _scrollCtrl.animateTo(_scrollCtrl.position.maxScrollExtent,
          duration: const Duration(milliseconds: 200), curve: Curves.easeOut);
    }
  }

  void _sendMessage() {
    final text = _inputCtrl.text.trim();
    if (text.isEmpty) return;
    ref.read(chatProvider.notifier).sendMessage(text);
    _inputCtrl.clear();
    _scrollToBottom();
  }

  Future<void> _pickFiles() async {
    final result = await FilePicker.platform.pickFiles(allowMultiple: true);
    if (result != null) setState(() => _attachedFiles = result.files);
  }

  @override
  Widget build(BuildContext context) {
    final chatState = ref.watch(chatProvider);
    final isEmpty = chatState.messages.isEmpty;

    return Scaffold(
      appBar: AppBar(title: const Text('标书分析助手')),
      body: Column(
        children: [
          if (isEmpty)
            Expanded(
              child: Column(
                children: [
                  const SizedBox(height: AppSpacing.xl),
                  QuickActionsBar(onTap: (prompt) {
                    _inputCtrl.text = prompt;
                    _sendMessage();
                  }),
                  const Expanded(
                    child: EmptyState(
                      icon: LucideIcons.messagesSquare,
                      title: '开始对话',
                      subtitle: '选择一个快捷操作或输入你的问题',
                    ),
                  ),
                ],
              ),
            )
          else
            Expanded(
              child: Column(
                children: [
                  QuickActionsBar(onTap: (prompt) {
                    _inputCtrl.text = prompt;
                    _sendMessage();
                  }),
                  Expanded(
                    child: ListView.builder(
                      controller: _scrollCtrl,
                      padding: const EdgeInsets.only(top: AppSpacing.sm, bottom: AppSpacing.lg),
                      itemCount: chatState.messages.length,
                      itemBuilder: (context, i) =>
                          MessageBubble(message: chatState.messages[i]),
                    ),
                  ),
                ],
              ),
            ),
          if (_attachedFiles != null && _attachedFiles!.isNotEmpty)
            Container(
              padding: const EdgeInsets.symmetric(horizontal: AppSpacing.lg),
              child: Wrap(
                children: _attachedFiles!.map((f) => Chip(
                  label: Text(f.name, style: const TextStyle(fontSize: 12)),
                  deleteIcon: const Icon(Icons.close, size: 16),
                  onDeleted: () => setState(() => _attachedFiles = null),
                )).toList(),
              ),
            ),
          ChatInputBar(
            controller: _inputCtrl,
            isLoading: chatState.isLoading,
            onSend: _sendMessage,
            onAttach: _pickFiles,
            onCancel: () => ref.read(chatProvider.notifier).cancelStream(),
          ),
        ],
      ),
    );
  }
}
```

- [ ] **Step 5: Commit**

```bash
cd D:\AI\ragflow2\bidding_app && git add lib/features/chat/ && git commit -m "feat: add chat UI (screen, bubbles, input bar, quick actions)"
```

---

### Task 10: Tools feature

**Files:**
- Create: `lib/features/tools/providers/tools_provider.dart`
- Create: `lib/features/tools/screens/tools_list_screen.dart`
- Create: `lib/features/tools/screens/calculator_screen.dart`

- [ ] **Step 1: Tools provider (static list + calculator logic)**

Write `lib/features/tools/providers/tools_provider.dart`:

```dart
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../models/tool_item.dart';

final toolsProvider = Provider<List<ToolItem>>((ref) => const [
  ToolItem(
    id: 'agency-fee',
    name: '代理费用计算',
    description: '按差额定率分档累进法计算招标代理服务费',
    icon: 'calculator',
  ),
  ToolItem(
    id: 'cost-consulting',
    name: '造价咨询服务费',
    description: '建设工程造价咨询服务费（14类项目）',
    icon: 'receipt',
  ),
  ToolItem(
    id: 'engineering-survey',
    name: '工程勘察设计费',
    description: '工程勘察设计收费标准（2002）10号',
    icon: 'compass',
  ),
  ToolItem(
    id: 'supervision-fee',
    name: '建设工程监理费',
    description: '建设工程监理收费 发改价格[2007]670号',
    icon: 'clipboard-check',
  ),
]);

// Agency fee tiered progressive calculation
class AgencyFeeState {
  final double bidAmount;
  final double? fee;

  const AgencyFeeState({this.bidAmount = 0, this.fee});
  AgencyFeeState copyWith({double? bidAmount, double? fee}) =>
      AgencyFeeState(bidAmount: bidAmount ?? this.bidAmount, fee: fee);
}

class AgencyFeeNotifier extends StateNotifier<AgencyFeeState> {
  AgencyFeeNotifier() : super(const AgencyFeeState());

  void calculate(double amount) {
    double fee = 0;
    double remaining = amount;

    final tiers = [
      (1000000, 0.015),   // 100万以下 1.5%
      (5000000, 0.011),   // 100-500万 1.1%
      (10000000, 0.008),  // 500-1000万 0.8%
      (50000000, 0.005),  // 1000-5000万 0.5%
      (100000000, 0.0025),// 5000万-1亿 0.25%
      (double.infinity, 0.001), // 1亿以上 0.1%
    ];

    double prevMax = 0;
    for (final (max, rate) in tiers) {
      if (remaining <= 0) break;
      final tierAmount = (amount > max ? max - prevMax : amount - prevMax).clamp(0, remaining);
      fee += tierAmount * rate;
      remaining -= tierAmount;
      prevMax = max;
    }

    state = AgencyFeeState(bidAmount: amount, fee: fee);
  }
}

final agencyFeeProvider = StateNotifierProvider<AgencyFeeNotifier, AgencyFeeState>(
  (ref) => AgencyFeeNotifier(),
);
```

- [ ] **Step 2: Tools list screen**

Write `lib/features/tools/screens/tools_list_screen.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:lucide_icons/lucide_icons.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_spacing.dart';
import '../../../core/theme/app_typography.dart';
import '../../../shared/widgets/app_card.dart';
import '../providers/tools_provider.dart';
import 'calculator_screen.dart';

class ToolsListScreen extends ConsumerWidget {
  const ToolsListScreen({super.key});

  IconData _iconFor(String name) {
    return switch (name) {
      'calculator' => LucideIcons.calculator,
      'receipt' => LucideIcons.receipt,
      'compass' => LucideIcons.compass,
      'clipboard-check' => LucideIcons.clipboardCheck,
      _ => LucideIcons.wrench,
    };
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final tools = ref.watch(toolsProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('工具列表')),
      body: ListView.builder(
        padding: const EdgeInsets.all(AppSpacing.lg),
        itemCount: tools.length,
        itemBuilder: (context, i) {
          final tool = tools[i];
          return Padding(
            padding: const EdgeInsets.only(bottom: AppSpacing.md),
            child: AppCard(
              onTap: () => Navigator.of(context).push(
                MaterialPageRoute(
                  builder: (_) => CalculatorScreen(tool: tool),
                ),
              ),
              child: Row(
                children: [
                  Container(
                    width: 44,
                    height: 44,
                    decoration: BoxDecoration(
                      color: AppColors.background,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Icon(_iconFor(tool.icon), size: 20, color: AppColors.primary),
                  ),
                  const SizedBox(width: AppSpacing.md),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(tool.name, style: AppTypography.bodyLarge.copyWith(color: AppColors.primary, fontWeight: FontWeight.w600)),
                        const SizedBox(height: 2),
                        Text(tool.description, style: AppTypography.bodySmall.copyWith(color: AppColors.muted)),
                      ],
                    ),
                  ),
                  Icon(LucideIcons.chevronRight, size: 18, color: AppColors.muted),
                ],
              ),
            ),
          );
        },
      ),
    );
  }
}
```

- [ ] **Step 3: Calculator screen (generic form)**

Write `lib/features/tools/screens/calculator_screen.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_spacing.dart';
import '../../../core/theme/app_typography.dart';
import '../../../models/tool_item.dart';
import '../providers/tools_provider.dart';

class CalculatorScreen extends ConsumerStatefulWidget {
  final ToolItem tool;
  const CalculatorScreen({super.key, required this.tool});

  @override
  ConsumerState<CalculatorScreen> createState() => _CalculatorScreenState();
}

class _CalculatorScreenState extends ConsumerState<CalculatorScreen> {
  final _amountCtrl = TextEditingController();

  @override
  void dispose() {
    _amountCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final feeState = ref.watch(agencyFeeProvider);

    return Scaffold(
      appBar: AppBar(title: Text(widget.tool.name)),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(AppSpacing.lg),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('输入招标金额（万元）', style: AppTypography.bodySmall.copyWith(color: AppColors.muted)),
            const SizedBox(height: AppSpacing.sm),
            TextField(
              controller: _amountCtrl,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(hintText: '请输入金额'),
              onChanged: (val) {
                final amount = double.tryParse(val) ?? 0;
                ref.read(agencyFeeProvider.notifier).calculate(amount * 10000);
              },
            ),
            if (feeState.fee != null) ...[
              const SizedBox(height: AppSpacing.xl),
              Container(
                padding: const EdgeInsets.all(AppSpacing.lg),
                decoration: BoxDecoration(
                  color: AppColors.primary,
                  borderRadius: BorderRadius.circular(AppSpacing.cardRadius),
                ),
                child: Column(
                  children: [
                    Text('¥ ${feeState.fee!.toStringAsFixed(2)}', style: AppTypography.displayLarge.copyWith(color: Colors.white)),
                    const SizedBox(height: AppSpacing.xs),
                    Text('预计服务费用', style: AppTypography.bodySmall.copyWith(color: Colors.white.withOpacity(0.7))),
                  ],
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
```

- [ ] **Step 4: Commit**

```bash
cd D:\AI\ragflow2\bidding_app && git add lib/features/tools/ && git commit -m "feat: add tools feature (list + agency fee calculator)"
```

---

### Task 11: Bid feature

**Files:**
- Create: `lib/features/bid/providers/bid_provider.dart`
- Create: `lib/features/bid/screens/bid_screen.dart`
- Create: `lib/features/bid/widgets/bid_card.dart`

- [ ] **Step 1: Bid provider**

Write `lib/features/bid/providers/bid_provider.dart`:

```dart
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/api/api_client.dart';
import '../../../models/bid_item.dart';

class BidState {
  final List<BidItem> items;
  final bool isLoading;
  final int total;
  final String query;
  final String category;

  const BidState({
    this.items = const [],
    this.isLoading = false,
    this.total = 0,
    this.query = '',
    this.category = 'bid',
  });

  BidState copyWith({List<BidItem>? items, bool? isLoading, int? total, String? query, String? category}) =>
      BidState(
        items: items ?? this.items,
        isLoading: isLoading ?? this.isLoading,
        total: total ?? this.total,
        query: query ?? this.query,
        category: category ?? this.category,
      );
}

class BidNotifier extends StateNotifier<BidState> {
  BidNotifier() : super(const BidState());

  static const _categories = [
    'bid-search', 'contracts', 'enterprises', 'construction', 'credit-china',
  ];

  void setCategory(String cat) => state = state.copyWith(category: cat);

  Future<void> search(String query) async {
    if (query.isEmpty) return;
    state = state.copyWith(isLoading: true, query: query);

    try {
      final resp = await ApiClient.dio.get('/api/v1/bid/search', queryParameters: {
        'q': query,
        'category': state.category,
      });
      final result = resp.data;
      if (result['code'] == 0) {
        final items = (result['data']?['items'] as List?)
            ?.map((e) => BidItem.fromJson(e))
            .toList() ?? [];
        state = state.copyWith(
          items: items,
          total: result['data']?['total'] ?? items.length,
          isLoading: false,
        );
      }
    } catch (e) {
      state = state.copyWith(isLoading: false);
    }
  }
}

final bidProvider = StateNotifierProvider<BidNotifier, BidState>(
  (ref) => BidNotifier(),
);
```

- [ ] **Step 2: Bid card**

Write `lib/features/bid/widgets/bid_card.dart`:

```dart
import 'package:flutter/material.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_spacing.dart';
import '../../../core/theme/app_typography.dart';
import '../../../models/bid_item.dart';

class BidCard extends StatelessWidget {
  final BidItem item;
  final VoidCallback? onTap;

  const BidCard({super.key, required this.item, this.onTap});

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: AppSpacing.lg, vertical: AppSpacing.xs),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(AppSpacing.cardRadius),
        side: const BorderSide(color: AppColors.border),
      ),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(AppSpacing.cardRadius),
        child: Padding(
          padding: const EdgeInsets.all(AppSpacing.lg),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(item.title, style: AppTypography.bodyLarge.copyWith(color: AppColors.primary, fontWeight: FontWeight.w600), maxLines: 2, overflow: TextOverflow.ellipsis),
              if (item.purchaser != null || item.publishDate != null) ...[
                const SizedBox(height: AppSpacing.sm),
                Row(
                  children: [
                    if (item.purchaser != null) ...[
                      Icon(Icons.business, size: 14, color: AppColors.muted),
                      const SizedBox(width: 4),
                      Expanded(child: Text(item.purchaser!, style: AppTypography.bodySmall.copyWith(color: AppColors.muted), overflow: TextOverflow.ellipsis)),
                    ],
                    const Spacer(),
                    if (item.publishDate != null)
                      Text(item.publishDate!, style: AppTypography.bodySmall.copyWith(color: AppColors.muted)),
                  ],
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
```

- [ ] **Step 3: Bid screen**

Write `lib/features/bid/screens/bid_screen.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/theme/app_colors.dart';
import '../../../core/theme/app_spacing.dart';
import '../../../shared/widgets/shimmer_loading.dart';
import '../../../shared/widgets/empty_state.dart';
import '../providers/bid_provider.dart';
import '../widgets/bid_card.dart';
import 'package:lucide_icons/lucide_icons.dart';

class BidScreen extends ConsumerStatefulWidget {
  const BidScreen({super.key});

  @override
  ConsumerState<BidScreen> createState() => _BidScreenState();
}

class _BidScreenState extends ConsumerState<BidScreen> {
  final _searchCtrl = TextEditingController();

  @override
  void dispose() {
    _searchCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(bidProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('招标信息')),
      body: Column(
        children: [
          // Search bar
          Padding(
            padding: const EdgeInsets.all(AppSpacing.lg),
            child: TextField(
              controller: _searchCtrl,
              onSubmitted: (q) => ref.read(bidProvider.notifier).search(q),
              decoration: InputDecoration(
                hintText: '搜索标讯...',
                prefixIcon: const Icon(LucideIcons.search, size: 18),
                suffixIcon: _searchCtrl.text.isNotEmpty
                    ? IconButton(
                        icon: const Icon(Icons.clear, size: 18),
                        onPressed: () {
                          _searchCtrl.clear();
                          ref.read(bidProvider.notifier).search('');
                        },
                      )
                    : null,
              ),
            ),
          ),

          // List
          Expanded(
            child: state.isLoading
                ? const ShimmerLoading()
                : state.items.isEmpty
                    ? const EmptyState(
                        icon: LucideIcons.search,
                        title: '搜索招标信息',
                        subtitle: '输入关键词搜索标讯、合同、企业等',
                      )
                    : RefreshIndicator(
                        onRefresh: () async => ref.read(bidProvider.notifier).search(state.query),
                        child: ListView.builder(
                          itemCount: state.items.length + (state.items.length < state.total ? 1 : 0),
                          itemBuilder: (context, i) {
                            if (i >= state.items.length) {
                              return const Center(child: CircularProgressIndicator());
                            }
                            return BidCard(item: state.items[i]);
                          },
                        ),
                      ),
          ),
        ],
      ),
    );
  }
}
```

- [ ] **Step 4: Commit**

```bash
cd D:\AI\ragflow2\bidding_app && git add lib/features/bid/ && git commit -m "feat: add bid feature (search, list, cards)"
```

---

### Task 12: App entry point — wire everything together

**Files:**
- Modify: `lib/main.dart`
- Create: `lib/app.dart`

- [ ] **Step 1: main.dart — ProviderScope + ApiClient init**

Write `lib/main.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'core/api/api_client.dart';
import 'app.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  ApiClient.init();
  runApp(const ProviderScope(child: BiddingApp()));
}
```

- [ ] **Step 2: app.dart — MaterialApp.router + theme**

Write `lib/app.dart`:

```dart
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'core/router/app_router.dart';
import 'core/theme/app_theme.dart';
import 'features/auth/providers/auth_provider.dart';

class BiddingApp extends ConsumerStatefulWidget {
  const BiddingApp({super.key});

  @override
  ConsumerState<BiddingApp> createState() => _BiddingAppState();
}

class _BiddingAppState extends ConsumerState<BiddingApp> {
  @override
  void initState() {
    super.initState();
    Future.microtask(() => ref.read(authProvider.notifier).tryAutoLogin());
  }

  @override
  Widget build(BuildContext context) {
    final auth = ref.watch(authProvider);

    return MaterialApp.router(
      title: '标书分析助手',
      theme: AppTheme.light(),
      darkTheme: AppTheme.dark(),
      themeMode: ThemeMode.system,
      routerConfig: appRouter,
      debugShowCheckedModeBanner: false,
    );
  }
}
```

- [ ] **Step 3: Verify router redirect for auth**

Modify `lib/core/router/app_router.dart` to add auth redirect:

Add at top of routes:
```dart
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../features/auth/providers/auth_provider.dart';

// Add redirect to GoRouter config:
final appRouter = GoRouter(
  // ... existing ...
  redirect: (context, state) {
    // Auth check handled at app level
    return null;
  },
);
```

- [ ] **Step 4: Commit**

```bash
cd D:\AI\ragflow2\bidding_app && git add lib/main.dart lib/app.dart lib/core/router/ && git commit -m "feat: wire up app entry point (ProviderScope + MaterialApp.router)"
```

---

### Task 13: Build verification & cleanup

**Files:** None new.

- [ ] **Step 1: Run flutter analyze**

```bash
cd D:\AI\ragflow2\bidding_app && flutter analyze
```
Expected: No issues found.

- [ ] **Step 2: Fix any analysis warnings**

Review output from Step 1 and apply fixes (missing imports, unused variables, etc.).

- [ ] **Step 3: Verify build for Android**

```bash
cd D:\AI\ragflow2\bidding_app && flutter build apk --debug
```
Expected: Build successful.

- [ ] **Step 4: Verify build for iOS** (macOS only, skip on Windows)

```bash
cd D:\AI\ragflow2\bidding_app && flutter build ios --debug --no-codesign
```

- [ ] **Step 5: Commit**

```bash
cd D:\AI\ragflow2\bidding_app && git add -A && git commit -m "chore: fix analysis issues and verify build"
```

---

## Completion Checklist

- [ ] `flutter analyze` passes with zero issues
- [ ] `flutter build apk --debug` succeeds
- [ ] Auth: login/register flow works against real backend
- [ ] Chat: SSE streaming displays token-by-token
- [ ] Tools: calculator produces correct results
- [ ] Bid: search returns results from API
- [ ] Bottom nav: 3 tabs switch correctly, state preserved
- [ ] Light/dark mode: both themes render correctly
- [ ] Safe areas: no content hidden behind notch/gesture bar
- [ ] Touch targets: all interactive elements ≥ 44pt
