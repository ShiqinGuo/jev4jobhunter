package jobhunter;

import android.os.Bundle;
import android.view.accessibility.AccessibilityNodeInfo;
import com.android.uiautomator.core.UiObject;
import com.android.uiautomator.core.UiSelector;
import com.android.uiautomator.testrunner.UiAutomatorTestCase;
import java.io.FileInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.util.ArrayList;
import java.util.List;
import android.graphics.Rect;
import org.json.JSONObject;
import org.json.JSONArray;

/** Normal accessibility text action on one explicitly selected visible BOSS field. */
public final class NativeText extends UiAutomatorTestCase {
    private static final class Editable extends UiObject {
        Editable(UiSelector selector) { super(selector); }
        boolean replace(String text) {
            AccessibilityNodeInfo node = findAccessibilityNodeInfo(3000);
            if (node == null || !node.isEditable() || !node.isVisibleToUser()) return false;
            Bundle arguments = new Bundle();
            arguments.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
            return node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments);
        }
        AccessibilityNodeInfo node() { return findAccessibilityNodeInfo(3000); }
    }

    private static List<AccessibilityNodeInfo> nodes(AccessibilityNodeInfo root, String resource) {
        List<AccessibilityNodeInfo> found = new ArrayList<>();
        visit(root, "com.hpbr.bosszhipin:id/" + resource, found);
        return found;
    }

    private static void visit(AccessibilityNodeInfo node, String resource, List<AccessibilityNodeInfo> found) {
        if (node == null) return;
        if (resource.equals(node.getViewIdResourceName())) found.add(node);
        for (int index = 0; index < node.getChildCount(); index++) visit(node.getChild(index), resource, found);
    }

    private static String text(AccessibilityNodeInfo node) { return String.valueOf(node.getText()); }

    private static JSONObject snapshot(AccessibilityNodeInfo editor) throws Exception {
        AccessibilityNodeInfo root = editor;
        while (root.getParent() != null) root = root.getParent();
        List<AccessibilityNodeInfo> companies = nodes(root, "tv_company_name");
        Rect screen = new Rect(); root.getBoundsInScreen(screen);
        String company = null;
        if (companies.size() == 1) company = text(companies.get(0));
        else for (AccessibilityNodeInfo header : nodes(root, "tv_sub_title")) {
            Rect bounds = new Rect(); header.getBoundsInScreen(bounds);
            if (bounds.bottom < screen.bottom*.12 && text(header).contains(" · ")) {
                if (company != null) throw new Exception("Ambiguous company header");
                company = text(header).split(" · ", 2)[0].trim();
            }
        }
        if (company == null) throw new Exception("Conversation company required");
        JSONArray messages = new JSONArray();
        for (AccessibilityNodeInfo content : nodes(root, "tv_content_text")) {
            AccessibilityNodeInfo parent = content.getParent();
            while (parent != null && parent != root) {
                List<AccessibilityNodeInfo> avatars = nodes(parent, "iv_avatar");
                if (!avatars.isEmpty()) {
                    if (avatars.size() != 1) throw new Exception("Ambiguous message direction");
                    Rect avatar = new Rect(); avatars.get(0).getBoundsInScreen(avatar);
                    boolean delivered = false;
                    for (AccessibilityNodeInfo status : nodes(parent, "mMsgTvStatus"))
                        if ("送达".equals(text(status))) delivered = true;
                    messages.put(new JSONObject().put("text", text(content))
                        .put("outbound", avatar.left > screen.right * .8).put("delivered", delivered));
                    break;
                }
                parent = parent.getParent();
            }
        }
        return new JSONObject().put("company", company).put("messages", messages);
    }

    private static AccessibilityNodeInfo sendControl(AccessibilityNodeInfo editor) throws Exception {
        Rect field = new Rect(); editor.getBoundsInScreen(field);
        AccessibilityNodeInfo group = editor.getParent();
        while (group != null && !"com.hpbr.bosszhipin:id/chat_functions".equals(group.getViewIdResourceName()))
            group = group.getParent();
        if (group == null) throw new Exception("Composer group required");
        List<AccessibilityNodeInfo> controls = new ArrayList<>();
        composerNodes(group, controls);
        List<SendTarget.Candidate> candidates = new ArrayList<>();
        for (AccessibilityNodeInfo node : controls) {
            Rect bounds = new Rect(); node.getBoundsInScreen(bounds);
            candidates.add(new SendTarget.Candidate(String.valueOf(node.getClassName()),
                node.isClickable(), node.isVisibleToUser(), bounds.left, bounds.top, bounds.right, bounds.bottom));
        }
        return controls.get(SendTarget.choose(candidates, field.right, field.top, field.bottom));
    }

    private static void composerNodes(AccessibilityNodeInfo node, List<AccessibilityNodeInfo> found) {
        if (node == null) return;
        found.add(node);
        for (int index = 0; index < node.getChildCount(); index++) composerNodes(node.getChild(index), found);
    }

    private static void write(File file, String value) throws Exception {
        try (FileOutputStream stream = new FileOutputStream(file)) {
            stream.write(value.getBytes("UTF-8")); stream.getFD().sync();
        }
    }

    public void testSetText() throws Exception {
        String resource = getParams().getString("resource");
        String file = getParams().getString("textFile");
        assertTrue("BOSS field required", resource != null && resource.startsWith("com.hpbr.bosszhipin:id/"));
        assertTrue("Owned text file required", file != null && file.startsWith("/data/local/tmp/job-hunter-text-"));
        UiSelector selector = new UiSelector().packageName("com.hpbr.bosszhipin").resourceId(resource);
        assertTrue("Unique editable field required", new UiObject(selector.instance(0)).exists()
                && !new UiObject(selector.instance(1)).exists());
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        try (FileInputStream stream = new FileInputStream(file)) {
            byte[] chunk = new byte[4096];
            int count;
            while ((count = stream.read(chunk)) != -1) buffer.write(chunk, 0, count);
        }
        assertTrue("Text size bounded", buffer.size() > 0 && buffer.size() <= 8192);
        String text = new String(buffer.toByteArray(), "UTF-8");
        assertTrue("Text action rejected", new Editable(selector).replace(text));
        boolean readBack = false;
        for (int attempt = 0; attempt < 10; attempt++) {
            sleep(200);
            if (text.equals(new UiObject(selector).getText())) { readBack = true; break; }
        }
        assertTrue("Text readback mismatch", readBack);
        String readyPath = getParams().getString("readyFile");
        String controlPath = getParams().getString("controlFile");
        String actionId = getParams().getString("actionId");
        assertTrue("Owned control paths required", readyPath.startsWith("/data/local/tmp/job-hunter-ready-")
                && controlPath.startsWith("/data/local/tmp/job-hunter-control-"));
        JSONObject ready = snapshot(new Editable(selector).node());
        ready.put("actionId", actionId);
        File readyFile = new File(readyPath);
        File temporary = new File(readyPath + ".tmp");
        write(temporary, ready.toString());
        assertTrue("Ready publication failed", temporary.renameTo(readyFile));
        File control = new File(controlPath);
        String command = "";
        for (int attempt = 0; attempt < 600; attempt++) {
            if (control.isFile()) {
                ByteArrayOutputStream bytes = new ByteArrayOutputStream();
                try (FileInputStream stream = new FileInputStream(control)) {
                    byte[] chunk = new byte[128]; int count;
                    while ((count = stream.read(chunk)) != -1) bytes.write(chunk, 0, count);
                }
                command = new String(bytes.toByteArray(), "UTF-8"); break;
            }
            sleep(50);
        }
        if (command.equals("cancel:" + actionId)) return;
        assertTrue("No matching commit permission", command.equals("commit:" + actionId));
        assertTrue("Draft changed before click", text.equals(new UiObject(selector).getText()));
        AccessibilityNodeInfo send = sendControl(new Editable(selector).node());
        Rect bounds = new Rect(); send.getBoundsInScreen(bounds);
        assertTrue("Send click failed", getUiDevice().click(bounds.centerX(), bounds.centerY()));
        sleep(500);
    }
}
