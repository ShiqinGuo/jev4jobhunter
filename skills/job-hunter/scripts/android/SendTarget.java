package jobhunter;

import java.util.List;

/** Composer geometry from accessibility, with no Android runtime dependency. */
public final class SendTarget {
    public static final class Candidate {
        final String className;
        final boolean clickable, visible;
        final int left, top, right, bottom;

        public Candidate(String className, boolean clickable, boolean visible,
                         int left, int top, int right, int bottom) {
            this.className = className; this.clickable = clickable; this.visible = visible;
            this.left = left; this.top = top; this.right = right; this.bottom = bottom;
        }
    }

    public static int choose(List<Candidate> candidates, int editorRight, int editorTop, int editorBottom) {
        int selected = -1;
        for (int index = 0; index < candidates.size(); index++) {
            Candidate node = candidates.get(index);
            int centerY = (node.top + node.bottom) / 2;
            if ("android.widget.ImageView".equals(node.className) && node.clickable && node.visible
                    && node.right > node.left && node.bottom > node.top
                    && node.left > editorRight && centerY >= editorTop && centerY <= editorBottom) {
                if (selected != -1) throw new IllegalArgumentException("Unique send icon required");
                selected = index;
            }
        }
        if (selected == -1) throw new IllegalArgumentException("Unique send icon required");
        return selected;
    }
}
