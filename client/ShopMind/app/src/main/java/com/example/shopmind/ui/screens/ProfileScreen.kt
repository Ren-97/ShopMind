package com.example.shopmind.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.InputChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.PlainTooltip
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TooltipAnchorPosition
import androidx.compose.material3.TooltipBox
import androidx.compose.material3.TooltipDefaults
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.rememberTooltipState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.navigation.NavController
import com.example.shopmind.domain.ProfileResponse
import com.example.shopmind.network.RestApi
import com.example.shopmind.ui.components.AddPreferenceDialog
import com.example.shopmind.ui.components.PrefCategory
import com.example.shopmind.ui.components.defaultPrefCategories
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject

/**
 * 个人资料页 — 看 / 改当前用户(顶栏 ✓ 那位)的 profile。
 *
 * 两类字段两种交互(对齐 ChatGPT Memory 心智):
 *   - 身份 + 收货 = 表单,用户手动填/改 → 攒一次 PATCH /profile
 *   - 消费档位 + preferences = 标签,**只能删不能改**;新增/更新只通过对话(update_preference)
 *     删除走 PATCH /profile 传 null(列 → SET NULL;preferences key → pop)
 *
 * 数据每次进入实时拉 GET /profile,所以对话里 Agent 写的偏好,下次打开即可见。
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun ProfileScreen(navController: NavController, onboarding: Boolean = false) {
    val rest = remember { RestApi() }
    val scope = rememberCoroutineScope()
    val snackbarHost = remember { SnackbarHostState() }

    var profile by remember { mutableStateOf<ProfileResponse?>(null) }
    var loading by remember { mutableStateOf(true) }
    var errorMsg by remember { mutableStateOf<String?>(null) }
    var saving by remember { mutableStateOf(false) }
    var addPrefOpen by remember { mutableStateOf(false) }
    // 品牌候选(添加偏好用):开放词表,从目录 facets 拉;失败静默兜底空
    var brands by remember { mutableStateOf<List<String>>(emptyList()) }

    // 身份 + 收货:本地可编辑(表单),初始化自首次拉取
    var gender by remember { mutableStateOf<String?>(null) }
    var age by remember { mutableStateOf("") }
    var heightCm by remember { mutableStateOf("") }
    var weightKg by remember { mutableStateOf("") }
    var recipientName by remember { mutableStateOf("") }
    var phone by remember { mutableStateOf("") }
    var address by remember { mutableStateOf("") }

    fun syncForm(p: ProfileResponse) {
        gender = p.gender
        age = p.age?.toString().orEmpty()
        heightCm = p.heightCm?.let { "%.0f".format(it) }.orEmpty()
        weightKg = p.weightKg?.let { "%.0f".format(it) }.orEmpty()
        recipientName = p.recipientName.orEmpty()
        phone = p.phone.orEmpty()
        address = p.address.orEmpty()
    }

    LaunchedEffect(Unit) {
        try {
            val p = rest.getProfile()
            profile = p
            syncForm(p)
        } catch (e: Exception) {
            errorMsg = e.message ?: "加载个人资料失败"
        } finally {
            loading = false
        }
    }

    LaunchedEffect(Unit) {
        runCatching { brands = rest.getFacets().categories.flatMap { it.brands }.distinct() }
    }

    // 删除一条标签(consumption_tier 列 / preferences key 或其 list 中一项)
    fun deleteTag(body: String) {
        scope.launch {
            try {
                profile = rest.patchProfile(body)
            } catch (e: Exception) {
                snackbarHost.showSnackbar(e.message ?: "删除失败")
            }
        }
    }

    // 添加偏好:list 字段与现有值合并去重后整列回写;单值 / consumption_tier 列直接覆盖
    fun addPreference(cat: PrefCategory, values: List<String>) {
        if (values.isEmpty()) return
        scope.launch {
            try {
                val body = if (cat.isColumn) {
                    buildJsonObject { put(cat.key, values.first()) }.toString()
                } else {
                    buildJsonObject {
                        putJsonObject("preferences") {
                            if (cat.isList) {
                                val existing = profile?.preferences?.get(cat.key)
                                    ?.let { jsonElementToStrings(it) }.orEmpty()
                                val merged = (existing + values).distinct()
                                putJsonArray(cat.key) { merged.forEach { add(it) } }
                            } else {
                                put(cat.key, values.first())
                            }
                        }
                    }.toString()
                }
                profile = rest.patchProfile(body)
                snackbarHost.showSnackbar("已添加")
            } catch (e: Exception) {
                snackbarHost.showSnackbar(e.message ?: "添加失败")
            }
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(if (onboarding) "完善资料" else "个人资料") },
                navigationIcon = {
                    IconButton(onClick = { navController.popBackStack() }) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
                actions = {
                    // 仅新建用户落地时给显式「跳过」:profile 全可选,不填也能直接开聊
                    if (onboarding) {
                        TextButton(onClick = { navController.popBackStack() }) {
                            Text("跳过")
                        }
                    }
                },
            )
        },
        bottomBar = {
            if (!loading && errorMsg == null) {
                Surface(tonalElevation = 3.dp) {
                    Button(
                        onClick = {
                            saving = true
                            scope.launch {
                                try {
                                    val body = buildJsonObject {
                                        put("gender", gender)
                                        put("age", age.trim().toIntOrNull())
                                        put("height_cm", heightCm.trim().toDoubleOrNull())
                                        put("weight_kg", weightKg.trim().toDoubleOrNull())
                                        put("recipient_name", recipientName.trim().ifBlank { null })
                                        put("phone", phone.trim().ifBlank { null })
                                        put("address", address.trim().ifBlank { null })
                                    }.toString()
                                    val updated = rest.patchProfile(body)
                                    profile = updated
                                    syncForm(updated)
                                    snackbarHost.showSnackbar("已保存")
                                } catch (e: Exception) {
                                    snackbarHost.showSnackbar(e.message ?: "保存失败")
                                } finally {
                                    saving = false
                                }
                            }
                        },
                        enabled = !saving,
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(16.dp),
                    ) { Text(if (saving) "保存中…" else "保存身份 / 收货信息") }
                }
            }
        },
        snackbarHost = { SnackbarHost(snackbarHost) },
    ) { padding ->
        when {
            loading -> Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding),
                contentAlignment = Alignment.Center,
            ) { CircularProgressIndicator() }

            errorMsg != null -> Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .padding(24.dp),
                contentAlignment = Alignment.Center,
            ) { Text(errorMsg!!, color = MaterialTheme.colorScheme.error) }

            else -> Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .verticalScroll(rememberScrollState())
                    .padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                // ── 身份信息(可编辑)──
                SectionTitle("身份信息")
                Text(
                    "性别",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(
                        selected = gender == "male",
                        onClick = { gender = if (gender == "male") null else "male" },
                        label = { Text("男") },
                    )
                    FilterChip(
                        selected = gender == "female",
                        onClick = { gender = if (gender == "female") null else "female" },
                        label = { Text("女") },
                    )
                }
                NumberField(value = age, onChange = { age = it }, label = "年龄")
                NumberField(value = heightCm, onChange = { heightCm = it }, label = "身高 (cm)")
                NumberField(value = weightKg, onChange = { weightKg = it }, label = "体重 (kg)")

                HorizontalDivider()

                // ── 收货信息(可编辑)──
                SectionTitle("收货信息")
                OutlinedTextField(
                    value = recipientName,
                    onValueChange = { recipientName = it },
                    label = { Text("收件人") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = phone,
                    onValueChange = { phone = it },
                    label = { Text("电话") },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone),
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = address,
                    onValueChange = { address = it },
                    label = { Text("收货地址") },
                    minLines = 2,
                    maxLines = 4,
                    modifier = Modifier.fillMaxWidth(),
                )

                HorizontalDivider()

                // ── 为你记住的(标签,只能删不能改;说明藏进 ⓘ)──
                Row(verticalAlignment = Alignment.CenterVertically) {
                    SectionTitle("为你记住的")
                    val tipState = rememberTooltipState(isPersistent = true)
                    TooltipBox(
                        positionProvider = TooltipDefaults.rememberTooltipPositionProvider(
                            TooltipAnchorPosition.Above,
                        ),
                        tooltip = {
                            PlainTooltip {
                                Text("聊天时我会悄悄记下你的偏好,让推荐更懂你~ 不准的点 ✕ 删掉就好。")
                            }
                        },
                        state = tipState,
                    ) {
                        IconButton(onClick = { scope.launch { tipState.show() } }) {
                            Icon(
                                Icons.Outlined.Info,
                                contentDescription = "说明",
                                modifier = Modifier.size(18.dp),
                                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }

                val prefs = profile?.preferences.orEmpty()
                val tier = profile?.consumptionTier
                if (tier.isNullOrBlank() && prefs.isEmpty()) {
                    Text(
                        "还没记住什么呢,多聊几句吧~",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                } else {
                    if (!tier.isNullOrBlank()) {
                        PrefGroup(label = "消费档位", values = listOf(tier)) {
                            deleteTag(
                                buildJsonObject { put("consumption_tier", JsonNull) }.toString()
                            )
                        }
                    }
                    prefs.forEach { (key, value) ->
                        val items = jsonElementToStrings(value)
                        if (items.isNotEmpty()) {
                            val isList = value is JsonArray
                            PrefGroup(label = PREF_LABELS[key] ?: key, values = items) { item ->
                                deleteTag(buildPrefDeleteBody(key, item, isList, items))
                            }
                        }
                    }
                }

                AssistChip(
                    onClick = { addPrefOpen = true },
                    label = { Text("添加偏好") },
                    leadingIcon = {
                        Icon(
                            Icons.Default.Add,
                            contentDescription = null,
                            modifier = Modifier.size(AssistChipDefaults.IconSize),
                        )
                    },
                )
            }
        }
    }

    if (addPrefOpen) {
        AddPreferenceDialog(
            categories = defaultPrefCategories(brands),
            onConfirm = { cat, values ->
                addPrefOpen = false
                addPreference(cat, values)
            },
            onDismiss = { addPrefOpen = false },
        )
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.titleMedium,
        fontWeight = FontWeight.SemiBold,
    )
}

@Composable
private fun NumberField(value: String, onChange: (String) -> Unit, label: String) {
    OutlinedTextField(
        value = value,
        onValueChange = onChange,
        label = { Text(label) },
        singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
        modifier = Modifier.fillMaxWidth(),
    )
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun PrefGroup(label: String, values: List<String>, onDelete: (String) -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(
            label,
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            values.forEach { v ->
                DeletableChip(text = v, onDelete = { onDelete(v) })
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DeletableChip(text: String, onDelete: () -> Unit) {
    InputChip(
        selected = false,
        onClick = onDelete,
        label = { Text(text) },
        trailingIcon = {
            Icon(
                Icons.Default.Close,
                contentDescription = "删除",
                modifier = Modifier.size(16.dp),
            )
        },
    )
}

private val PREF_LABELS: Map<String, String> = mapOf(
    "skin_type" to "肤质",
    "skin_concerns" to "护肤需求",
    "fragrance_pref" to "香味",
    "brand_prefer" to "喜欢的品牌",
    "brand_exclude" to "不想要的品牌",
    "usage" to "常用场景",
    "os_pref" to "系统",
    "clothing_size" to "衣服尺码",
    "shoe_size" to "鞋码",
    "style_pref" to "风格",
    "dietary_restrictions" to "饮食",
)

private fun jsonElementToStrings(el: JsonElement): List<String> = when (el) {
    is JsonArray -> el.mapNotNull { (it as? JsonPrimitive)?.content }
    is JsonPrimitive -> listOf(el.content)
    else -> emptyList()
}

/**
 * 删除一条偏好:
 *   - 标量(非 list)→ pop 整个 key(传 null)
 *   - list 中一项 → 删该项后回写剩余;删空则 pop 整个 key
 */
private fun buildPrefDeleteBody(
    key: String,
    item: String,
    isList: Boolean,
    currentItems: List<String>,
): String = buildJsonObject {
    putJsonObject("preferences") {
        if (!isList) {
            put(key, JsonNull)
        } else {
            val remaining = currentItems.filterNot { it == item }
            if (remaining.isEmpty()) {
                put(key, JsonNull)
            } else {
                putJsonArray(key) { remaining.forEach { add(it) } }
            }
        }
    }
}.toString()
