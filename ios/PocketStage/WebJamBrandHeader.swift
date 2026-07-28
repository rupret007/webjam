import SwiftUI

/// Pocket Stage's visible link to the same continuous trefoil as the desktop.
struct WebJamBrandHeader: View {
    var body: some View {
        HStack(spacing: 12) {
            Image("WebJamMark")
                .resizable()
                .scaledToFit()
                .frame(width: 40, height: 40)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 1) {
                Text("WebJam")
                    .font(.headline)
                Text("Pocket Stage")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            Spacer()
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("WebJam Pocket Stage")
    }
}
